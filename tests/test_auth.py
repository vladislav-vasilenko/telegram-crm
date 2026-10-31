import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon import errors, types

from telegram_crm.store import Store
from telegram_crm.telegram import TelegramService


def test_delivery_reports_service_chat_and_server_resend_timer(tmp_path):
    store=Store(tmp_path)
    service=TelegramService(store)
    sent=types.auth.SentCode(type=types.auth.SentCodeTypeApp(length=5),phone_code_hash='private-hash',next_type=types.auth.CodeTypeSms(),timeout=60)
    service.describe_delivery(sent)
    assert 'служебный чат Telegram' in service.delivery['message']
    assert 'private-hash' not in str(service.delivery)
    assert service.delivery['can_resend']
    service.auth_step,service.phone,service.phone_code_hash='code','+79990000000','private-hash'
    with pytest.raises(ValueError,match='таймера'):
        asyncio.run(service.resend_code())
    store.close()


def test_unsupported_firebase_explains_qr_instead_of_claiming_sms(tmp_path):
    store=Store(tmp_path)
    service=TelegramService(store)
    sent=SimpleNamespace(type=SimpleNamespace(),timeout=0,next_type=None)
    with pytest.raises(ValueError,match='QR'):
        service.describe_delivery(sent)
    store.close()


def test_qr_waiter_starts_before_token_is_exposed_and_cancels(tmp_path):
    async def scenario():
        store=Store(tmp_path)
        service=TelegramService(store)
        registered=asyncio.Event()
        class FakeQR:
            url='tg://login?token=SYNTHETIC_TEST_TOKEN'
            async def wait(self):
                registered.set()
                await asyncio.Future()
        fake=SimpleNamespace(is_user_authorized=AsyncMock(return_value=False),set_receive_updates=AsyncMock(),qr_login=AsyncMock(return_value=FakeQR()))
        service.get_client=AsyncMock(return_value=fake)
        result=await service.begin_qr()
        assert result=={'step':'qr'} and registered.is_set()
        assert service.qr_image.startswith('data:image/png;base64,')
        await service.cancel_qr()
        assert service.qr_image is None and service.auth_step=='phone'
        store.close()
    asyncio.run(scenario())


def test_qr_starts_when_unauthorized_key_rejects_get_state(tmp_path):
    # Telegram answers updates.getState with AUTH_KEY_UNREGISTERED before login; QR must still start.
    async def scenario():
        store=Store(tmp_path)
        service=TelegramService(store)
        class FakeQR:
            url='tg://login?token=SYNTHETIC_TEST_TOKEN'
            async def wait(self):
                await asyncio.Future()
        failing=AsyncMock(side_effect=errors.AuthKeyUnregisteredError(request=None))
        fake=SimpleNamespace(is_user_authorized=AsyncMock(return_value=False),set_receive_updates=failing,qr_login=AsyncMock(return_value=FakeQR()))
        service.get_client=AsyncMock(return_value=fake)
        assert await service.begin_qr()=={'step':'qr'}
        assert service.qr_image.startswith('data:image/png;base64,')
        await service.cancel_qr()
        store.close()
    asyncio.run(scenario())


def test_firebase_sms_keeps_code_step_and_offers_resend(tmp_path):
    store=Store(tmp_path)
    service=TelegramService(store)
    sent=types.auth.SentCode(type=types.auth.SentCodeTypeFirebaseSms(length=5),phone_code_hash='h',next_type=types.auth.CodeTypeSms(),timeout=0)
    service.describe_delivery(sent)
    assert service.delivery['can_resend'] and 'QR' in service.delivery['message']
    store.close()
