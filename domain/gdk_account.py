import greenaddress as gdk
import json
from typing import List, Dict, Tuple
from domain.address_details import AddressDetails
from domain.gdk_utils import gdk_resolve
from domain.receiver import Receiver, receiver_to_dict
from domain.unspents_locker import Outpoint, UtxosLocker
from domain.utxo import CoinSelectionResult, Utxo

# fetch_subaccount() is using to get the subaccount JSON object from a GDK session
def fetch_subaccount(session: gdk.Session, accountID: str):
    subaccounts = session.get_subaccounts().resolve()
    subaccount = None
    
    for account in subaccounts['subaccounts']:
        if accountID == account['name']:
            subaccount = account
            
    if not subaccount:
        raise Exception(f'Cannot find the sub account with name: "{accountID}"')
    
    return subaccount

class GdkAccount():
    def __init__(self, session: gdk.Session, accountKey: str):
        self.session = session
        self.accountKey = accountKey
        self.locker = UtxosLocker()
        subaccount = fetch_subaccount(session, self.accountKey)
        self.pointer = subaccount['pointer']
        self.type = subaccount['type']
        self.gaid = self.session.get_subaccount(self.pointer).resolve()['receiving_id']
    
    def get_balance(self) -> int:
        return self.session.get_balance({'subaccount': self.pointer, 'num_confs': 0}).resolve()

    def get_new_address(self) -> AddressDetails:
        return self.session.get_receive_address({'subaccount': self.pointer}).resolve()

    def _get_previous_addresses(self, last_pointer: int) -> Tuple[List[AddressDetails], int]:
        previous_addresses = self.session.get_previous_addresses({'subaccount': self.pointer, last_pointer: last_pointer}).resolve()
        return previous_addresses['list'], previous_addresses['last_pointer']

    def list_all_addresses(self) -> List[AddressDetails]:
        last_pointer = 0
        addresses: List[AddressDetails] = []
        while last_pointer != 1:
            new_addresses, last_pointer = self._get_previous_addresses(last_pointer)
            addresses.extend(new_addresses)
        
        return addresses

    def _lock_coin_selection(self, coin_selection: CoinSelectionResult):
        for utxo in coin_selection.utxos:
            self.locker.lock(utxo)

    def select_utxos(self, asset: str, amount: int) -> CoinSelectionResult: 
        all_utxos_for_asset = self.get_all_utxos(True)[asset]
        total = 0
        selected_utxos: List[Utxo] = []
        for utxo in all_utxos_for_asset:
            selected_utxos.append(utxo)
            total += utxo.value
            
            if total >= amount:
                break
        
        result = CoinSelectionResult(total=total, utxos=selected_utxos, asset=asset, change=total-amount)
        self._lock_coin_selection(result)
        return result
            
    def get_all_utxos(self, only_unlocked: bool) -> Dict[str, List[Utxo]]:
        unspents = self._get_unspent_outputs(only_unlocked)
        utxos: Dict[str, List[Utxo]] = {}
        for asset, asset_unspents in unspents.items():
            asset_utxos = [Utxo(
                txid=utxo['txhash'], 
                index=utxo['pt_idx'], 
                asset=asset, 
                value=utxo['satoshi'], 
                script=utxo['script'], 
                is_confirmed=True, 
                is_locked=self.locker.is_locked(Outpoint(txid=utxo["txhash"], index=utxo["pt_idx"]))
            ) for utxo in asset_unspents]
            utxos[asset] = asset_utxos
        
        return utxos

    def _get_unspent_outputs(self, only_unlocked: bool) -> Dict[str, List[Dict]]:
        details = {
            'subaccount': self.pointer,
            'num_confs': 0,
        }
        
        result = gdk_resolve(gdk.get_unspent_outputs(self.session.session_obj, json.dumps(details)))
        unspent_outputs = result["unspent_outputs"]
        
        if not only_unlocked:
            return unspent_outputs
        
        available_utxos = {}
        for asset, asset_unspents in unspent_outputs.items():
            available_utxos[asset] = [unspent for unspent in asset_unspents if self.locker.is_locked(Outpoint(txid=unspent['txhash'], index=unspent['pt_idx'])) == False]

        return available_utxos        
        
    def get_transactions(self, min_height: int = None) -> List[Dict]:
        # We'll use possible statuses of UNCONFIRMED, CONFIRMED, FINAL.
        all_txs = []
        index = 0
        # You can override the default number (30) of transactions returned:
        count = 30
        while(True):
            transactions = self.session.get_transactions({'subaccount': self.pointer, 'first': index, 'count': count}).resolve()
            for transaction in transactions['transactions']:
                if min_height and transaction['block_height'] >= min_height:
                    all_txs.append(transaction)

            nb_txs = len(transactions['transactions'])

            if nb_txs < count:
                break
    
            if min_height and transactions['transactions'][nb_txs - 1]['block_height'] < min_height:
                break

            index = index + 1
        
        return all_txs
        
    def send(self, receivers: List[Receiver]) -> str:
        details = {
            'subaccount': self.pointer,
            'addresses': [map(lambda receiver: receiver_to_dict(receiver), receivers)],
            'utxos': self._get_unspent_outputs(True)
        }

        details = gdk_resolve(gdk.create_transaction(self.session.session_obj, json.dumps(details)))
        details = gdk_resolve(gdk.sign_transaction(self.session.session_obj, json.dumps(details)))
        hex = details['transaction']
        details = gdk_resolve(gdk.send_transaction(self.session.session_obj, json.dumps(details)))
        return hex
