from binascii import hexlify
import logging
from typing import List, Tuple 
from domain.gdk import GdkAPI, TransactionDetails
from domain.locker import Locker
from domain.receiver import Receiver
from domain.types import CoinSelectionResult, GdkUtxo, InputBlindingData, Outpoint, Utxo 
import greenaddress as gdk
import wallycore as wally
import secrets

# For each output to blind, we need 32 bytes of entropy for each of:
# - Output assetblinder
# - Output amountblinder
# - Ephemeral rangeproof ECDH key
# - Explicit value rangeproof
# - Surjectionproof seed
OUTPUT_ENTROPY_SIZE = 5 * 32

class TransactionService:
    def __init__(self, session: gdk.Session, locker: Locker) -> None:
        self._session = session
        self._gdk_api = GdkAPI(session)
        self._locker = locker

    def sign_transaction(self, txHex: str) -> str:
        pass

    def broadcast_transaction(self, txHex: str) -> str:
        return self._gdk_api.broadcast(txHex)

    def analyze_pset(self, psetBase64):
        details = {
            'psbt': psetBase64,
            'utxos': self._gdk_api.get_all_unspents_outputs(),
        }
        return self._session.psbt_get_details(details)

    def create_empty_pset(self) -> str:
        pset = wally.psbt_init(2, 0, 0, 0, wally.WALLY_PSBT_INIT_PSET)
        return wally.psbt_to_base64(pset, 0)

    def blind_pset(self, psetBase64: str) -> str:
        psbt = wally.psbt_from_base64(psetBase64)
        inputs_blinding_data = self._get_inputs_blinding_data(psetBase64)

        valueBlindingFactors, values, assetBlindingFactors, assets = [wally.map_init(len(inputs_blinding_data), None) for _ in range(4)]
        for i, blinding_data in enumerate(inputs_blinding_data):
            wally.map_add_integer(values, i, blinding_data['value'])
            wally.map_add_integer(valueBlindingFactors, i, blinding_data['value_blinder'])
            wally.map_add_integer(assets, i, blinding_data['asset'])
            wally.map_add_integer(assetBlindingFactors, i, blinding_data['asset_blinder'])

        blinder_indexes_to_blind = set([u['input_index'] for u in inputs_blinding_data])
        num_outputs = wally.psbt_get_num_outputs(psbt)
        num_outputs_to_blind = 0
        for i in range(num_outputs):
            if wally.psbt_get_output_script_len(psbt, i) == 0:
                continue # skip the fee outputs
            out_blinder_index = wally.psbt_get_output_blinder_index(psbt, i)
            if out_blinder_index in blinder_indexes_to_blind:
                if wally.psbt_get_output_blinding_public_key_len(psbt, i) == 0:
                    continue # skip the outputs without blinding public key
                num_outputs_to_blind += 1

        entropy = secrets.token_bytes(num_outputs_to_blind * 5 * 32)

        wally.psbt_blind(psbt, values, valueBlindingFactors,
                         assets, assetBlindingFactors, entropy, 0)
        
        return wally.psbt_to_base64(psbt, 0)

    def sign_pset(self, psetBase64: str) -> str:
        psbt = wally.psbt_from_base64(psetBase64)
        outputs_len = wally.psbt_get_num_outputs(psbt)
        for i in range(outputs_len):
            out_status = wally.psbt_get_output_blinding_status(psbt, i, 0)
            # raise an error if blinding is required
            blinding_status_guard_sign(i, out_status)

        utxos = self._gdk_api.get_all_utxos()
        utxos_to_sign: List[Tuple[int, Utxo]] = []
        
        inputs_len = wally.psbt_get_num_inputs(psbt)
        for input_index in range(inputs_len):
            input_txid = b2h_rev(wally.psbt_get_input_previous_txid(psbt, input_index))
            input_vout = wally.psbt_get_input_output_index(psbt, input_index)
            for u in utxos:
                if u.txid == input_txid and u.index == input_vout:
                    utxos_to_sign.append((input_index, u))
    
        if len(utxos_to_sign) == 0:
            raise Exception("No inputs to sign")
    
        blinding_nonces = []
        
        for out_index in range(outputs_len):
            if wally.psbt_get_output_ecdh_public_key_len(psbt, out_index) == 0:
                blinding_nonces.append('')
                continue
            blinding_nonces.append(wally.hex_from_bytes(wally.psbt_get_output_ecdh_public_key(psbt, out_index))[2:])
    
        num_in_signed = 0
        for i, _ in utxos_to_sign:
            try:
                utxos_arr = []
                for in_index, u in utxos_to_sign:
                    utxos_arr.append(skipped_utxo(u.gdk_utxo) if i != in_index else u.gdk_utxo)
                
                signed_result = self._gdk_api.sign_pset(psetBase64, utxos_arr, blinding_nonces)   
                psetBase64 = signed_result['psbt']
                num_in_signed += 1
            except Exception as e:
                logging.warning('Failed to sign input {}, reason: {}'.format(in_index, e))
                continue
            
        if num_in_signed == 0:
            raise Exception("No inputs has been signed")    
        
        return psetBase64

    def transfer(self, account_key: str, receivers: List[Receiver]) -> str:
        account = self._gdk_api.get_account(account_key)
        return account.send(receivers)

    def select_utxos(self, account_key: str, asset: str, amount: int) -> CoinSelectionResult:
        account = self._gdk_api.get_account(account_key)
        try:
            utxos_for_asset = account.utxos()[asset]
        except KeyError:
            raise Exception("No UTXOs found for asset {}".format(asset))
        utxos = [u for u in utxos_for_asset if not self._locker.is_locked(Outpoint.from_utxo(u))]
        selected_amount = 0
        selected_utxos = []
        for u in utxos:
            if selected_amount >= amount:
                break
            selected_utxos.append(u)
            selected_amount += u.value
            print('Selected UTXO: {}'.format(u.value))
        
        if selected_amount < amount:
            raise Exception("Not enough funds")
    
        result = CoinSelectionResult(asset, selected_amount, selected_amount - amount, selected_utxos) 
        for utxo in result.utxos:
            self._locker.lock(utxo, account_key)
        return result

    def estimate_fees(self) -> int:
        wallet = self._wallet_svc.get_wallet()
        fees = wallet.session.get_fee_estimates()["fees"]
        return fees[1]  # 1 block confirmation, 0 is min-relay-fees

    def get_transaction(self, txid: str) -> TransactionDetails:
        return self._gdk_api.get_transaction(txid)

    def _get_inputs_blinding_data(self, psbtb64: str) -> List[InputBlindingData]:
        blinding_data = []
        utxos = self._gdk_api.get_all_utxos()
        psbt = wally.psbt_from_base64(psbtb64)
        num_inputs = wally.psbt_get_num_inputs(psbt)
        for input_index in range(num_inputs):
            input_txid = b2h_rev(wally.psbt_get_input_previous_txid(psbt, input_index))
            input_vout = wally.psbt_get_input_output_index(psbt, input_index)
            for u in utxos:
                if u.txid == input_txid and u.index == input_vout:
                    blinding_data.append(u.to_blinding_data(input_index))
                    break
        return blinding_data

def blinding_status_guard_sign(output_index: int, blinding_status: int):
    if blinding_status == wally.WALLY_PSET_BLINDED_NONE:
        return
    if blinding_status == wally.WALLY_PSET_BLINDED_REQUIRED:
        raise Exception('Output ' + str(output_index) +
                        ' is required to be blinded (has blinding pub key)')
    if blinding_status == wally.WALLY_PSET_BLINDED_PARTIAL:
        raise Exception('Output ' + str(output_index) +
                        ' is partially blinded')
    if blinding_status == wally.WALLY_PSET_BLINDED_FULL:
        return

def b2h_rev(b: bytes) -> str:
    return wally.hex_from_bytes(b[::-1])

def skipped_utxo(u: GdkUtxo) -> GdkUtxo:
    cpy = u
    cpy['skip_signing'] = True
    return u