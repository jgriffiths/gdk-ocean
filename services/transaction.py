import logging
from typing import List, Tuple
from domain import block_details
from domain.receiver import Receiver
from domain.utxo import CoinSelectionResult
import greenaddress as gdk
from ocean import v1alpha as proto
from services.wallet import WalletService

class TransactionService:
    def __init__(self, wallet_svc: WalletService) -> None:
        self._wallet_svc = wallet_svc
    
    def sign_transaction(self, txHex: str) -> str:
        pass
    
    def broadcast_transaction(self, txHex: str) -> str:
        wallet = self._wallet_svc.get_wallet()
        return gdk.broadcast_transaction(wallet.session, txHex)
    
    def blind_pset(self, psetBase64: str) -> str:
        wallet = self._wallet_svc.get_wallet()
        pass
    
    def sign_pset(self, psetBase64: str) -> str:
        wallet = self._wallet_svc.get_wallet()
        pass

    def transfer(self, account_key: str, receivers: List[Receiver]) -> str:
        wallet = self._wallet_svc.get_wallet()
        account = wallet.get_account(account_key)
        return account.send(receivers)
    
    def select_utxos(self, account_key: str, asset: str, amount: int) -> CoinSelectionResult:
        wallet = self._wallet_svc.get_wallet()
        account = wallet.get_account(account_key)
        return account.select_utxos(asset, amount)
        
    def estimate_fees(self) -> int:
        wallet = self._wallet_svc.get_wallet()
        fees = wallet.session.get_fee_estimates()["fees"]
        return fees[1] # 1 block confirmation, 0 is min-relay-fees

    def get_transaction(self, txid: str) -> Tuple[str, proto.BlockDetails]:
        wallet = self._wallet_svc.get_wallet()
        wallet._get_tx_status(txid)
        tx_hex = wallet.get_transaction_hex(txid)
        block_details = wallet.get_block_details(txid)
        return tx_hex, block_details