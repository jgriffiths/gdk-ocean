from binascii import unhexlify
import pytest
import wallycore as wally
from domain import FilePinDataRepository, Asset, make_session, Locker, add_input_utxo, GdkAPI, InMemoryPinDataRepository

from services.transaction import TransactionService
from services.wallet import WalletService
from services.account import AccountService

TEST_PASSWORD = 'testdonotuse'
TEST_MNEMONIC = 'merit choice antique call primary wise first foot fold fire right share'

def test_create_pset():
    session = make_session('testnet-liquid')
    transactionSvc = TransactionService(session, Locker())
    psetb64 = transactionSvc.create_empty_pset()
    pset = wally.psbt_from_base64(psetb64)
    assert pset is not None
    outputs_len = wally.psbt_get_num_outputs(pset)
    inputs_len = wally.psbt_get_num_inputs(pset)
    assert inputs_len == 0
    assert outputs_len == 0

@pytest.mark.asyncio
async def test_send_pset():
    accountName = 'mainAccountTest'
    session = make_session('testnet-liquid')
    locker = await Locker.create()
    transactionSvc = TransactionService(session, locker)
    accountSvc = AccountService(session, locker)
    walletSvc = WalletService(session, InMemoryPinDataRepository())
    walletSvc.create_wallet(TEST_MNEMONIC, TEST_PASSWORD)

    addrs = accountSvc.derive_address(accountName, 2)
    coinSelection = transactionSvc.select_utxos(accountName, '144c654344aa716d6f3abcc1ca90e5641e4e2a7f633bc09fe3baf64585819a49', 100000)
    FEE = 500
    
    gdkAPI = GdkAPI(session)
    psetb64 = transactionSvc.create_empty_pset()
    for utxo in coinSelection.utxos:
        psetb64 = add_input_utxo(gdkAPI, psetb64, utxo.gdk_utxo)

    pset = wally.psbt_from_base64(psetb64)
    lbtc = Asset.from_hex(utxo.asset).to_bytes()
    
    outputSend = wally.tx_elements_output_init(
        unhexlify(addrs[0]['blinding_script']),
        lbtc,
        wally.tx_confidential_value_from_satoshi(coinSelection.amount-FEE),
    )
    wally.psbt_add_tx_output_at(pset, 0, 0, outputSend)
    blindingPubKey = wally.confidential_addr_to_ec_public_key(addrs[0]['address'], wally.WALLY_CA_PREFIX_LIQUID_TESTNET)
    wally.psbt_set_output_blinding_public_key(pset, 0, blindingPubKey)
    wally.psbt_set_output_blinder_index(pset, 0, 0)
    
    nextIndex = 1
    
    if coinSelection.change > 0:
        outputChange = wally.tx_elements_output_init(
            unhexlify(addrs[1]['blinding_script']),
            lbtc,
            wally.tx_confidential_value_from_satoshi(coinSelection.change),
        )
        wally.psbt_add_tx_output_at(pset, nextIndex, 0, outputChange)
        blindingPubKey = wally.confidential_addr_to_ec_public_key(addrs[1]['address'], wally.WALLY_CA_PREFIX_LIQUID_TESTNET)
        wally.psbt_set_output_blinding_public_key(pset, nextIndex, blindingPubKey)
        wally.psbt_set_output_blinder_index(pset, nextIndex, 0)
        nextIndex += 1

    outputFee = wally.tx_elements_output_init(
        None,
        lbtc,
        wally.tx_confidential_value_from_satoshi(FEE),
    )
    wally.psbt_add_tx_output_at(pset, nextIndex, 0, outputFee)

    b64 = wally.psbt_to_base64(pset, 0)
    blinded = transactionSvc.blind_pset(b64)
    signed = transactionSvc.sign_pset(blinded)
    assert signed is not None

    pset = wally.psbt_from_base64(signed)
    wally.psbt_finalize(pset)
    tx = wally.psbt_extract(pset)
    assert tx is not None
    
@pytest.mark.asyncio
async def test_send_amp_confidential_pset():
    accountName = 'mainAccountTest'
    ampAccountName = 'ampAccountTest'
    session = make_session('testnet-liquid')
    walletSvc = WalletService(session, FilePinDataRepository('pin_data.json'))
    walletSvc.create_wallet(TEST_MNEMONIC, TEST_PASSWORD)
    # walletSvc.login_with_pin(TEST_PASSWORD)
    lockerSvc = await Locker.create()
    accountSvc = AccountService(session, lockerSvc)
    transactionSvc = TransactionService(session, lockerSvc)

    FEE = 500
    fees_selection = transactionSvc.select_utxos(accountName, '144c654344aa716d6f3abcc1ca90e5641e4e2a7f633bc09fe3baf64585819a49', FEE)
    amp_selection = transactionSvc.select_utxos(ampAccountName, 'bea126b86ac7f7b6fc4709d1bb1a8482514a68d35633a5580d50b18504d5c322', 1)
    amp_utxo = amp_selection.utxos[0]
    
    receiveAddr = 'vjTvPHdcJFZrYL9LmFpPootd1tmWqzugF9MXwhet6cdeCKEK6WJrb2mPEQGw7WNpikAoTq9ui22GU2pS' 
    unconf = wally.confidential_addr_to_addr(receiveAddr, wally.WALLY_CA_PREFIX_LIQUID_TESTNET)
    receiveScript = wally.address_to_scriptpubkey(unconf, wally.WALLY_NETWORK_LIQUID_TESTNET)
    blindingPubKey = wally.confidential_addr_to_ec_public_key(receiveAddr, wally.WALLY_CA_PREFIX_LIQUID_TESTNET)

    gdkAPI = GdkAPI(session)
    psetb64 = add_input_utxo(gdkAPI, transactionSvc.create_empty_pset(), amp_utxo.gdk_utxo)
    for utxo in fees_selection.utxos:
        psetb64 = add_input_utxo(gdkAPI, psetb64, utxo.gdk_utxo)

    pset = wally.psbt_from_base64(psetb64)
    amp_asset = Asset.from_hex('bea126b86ac7f7b6fc4709d1bb1a8482514a68d35633a5580d50b18504d5c322').to_bytes()
    lbtc = Asset.from_hex('144c654344aa716d6f3abcc1ca90e5641e4e2a7f633bc09fe3baf64585819a49').to_bytes()
    
    output0 = wally.tx_elements_output_init(
        receiveScript,
        amp_asset,
        wally.tx_confidential_value_from_satoshi(amp_utxo.value),
    )
    wally.psbt_add_tx_output_at(pset, 0, 0, output0)
    wally.psbt_set_output_blinding_public_key(pset, 0, blindingPubKey)
    wally.psbt_set_output_blinder_index(pset, 0, 0)
    
    if fees_selection.change > 0:
        changeAddr = accountSvc.derive_address(accountName, 1)[0]
        changeScript = changeAddr['blinding_script']
        outputChange = wally.tx_elements_output_init(
            unhexlify(changeScript),
            lbtc,
            wally.tx_confidential_value_from_satoshi(fees_selection.change),
        )
        wally.psbt_add_tx_output_at(pset, 1, 0, outputChange)
        blindingPubKey = wally.confidential_addr_to_ec_public_key(changeAddr['address'], wally.WALLY_CA_PREFIX_LIQUID_TESTNET)
        wally.psbt_set_output_blinding_public_key(pset, 1, blindingPubKey)
        wally.psbt_set_output_blinder_index(pset, 1, 0)
    
    output_fee = wally.tx_elements_output_init(
        None,
        lbtc,
        wally.tx_confidential_value_from_satoshi(FEE),
    )
    wally.psbt_add_tx_output_at(pset, 2, 0, output_fee)

    b64 = wally.psbt_to_base64(pset, 0)
    blinded = transactionSvc.blind_pset(b64)
    signed = transactionSvc.sign_pset(blinded)    
    assert signed is not None

    pset = wally.psbt_from_base64(signed)
    wally.psbt_finalize(pset)
    tx = wally.psbt_extract(pset)
    assert tx is not None
    