import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon import errors, functions, types

from telegram_crm.models import Chat, Message, Settings
from telegram_crm.store import Store, utcnow
from datetime import timedelta

from telegram_crm.telegram import FULL_REFRESH_AFTER, HISTORY_DELAY_MIN, TelegramService


def test_scan_reads_without_marking_read_or_sending(tmp_path, monkeypatch):
    store=Store(tmp_path)
    service=TelegramService(store)
    raw=types.Message(id=5,peer_id=types.PeerUser(123),date=utcnow(),message='Привет',out=False)
    dialog=SimpleNamespace(id=123,name='Контакт',entity=types.User(id=123,first_name='Контакт'),message=raw,
                           input_entity=types.InputPeerUser(123,42),is_user=True,is_group=False,is_channel=False,
                           pinned=True,archived=False,unread_count=3)
    class ReadOnlyClient:
        history_calls=0
        async def iter_dialogs(self,**kwargs):
            yield dialog
        async def iter_messages(self,*args,**kwargs):
            self.history_calls+=1
            yield raw
    fake=ReadOnlyClient()
    service.get_client=AsyncMock(return_value=fake)
    monkeypatch.setattr('telegram_crm.telegram.asyncio.sleep',AsyncMock())
    asyncio.run(service.scan())
    saved=store.get('123')['chat']
    assert saved.messages[0].text=='Привет' and saved.unread_count==3 and saved.pinned
    assert store.metadata('last_scan_at') and not service.status['error']
    asyncio.run(service.scan())
    assert fake.history_calls==1  # unchanged history reuses cache
    raw.message='Отредактировано'
    asyncio.run(service.scan())
    assert fake.history_calls==1  # an edit of the newest message arrives with the dialog list
    assert store.get('123')['chat'].messages[0].text=='Отредактировано'
    store.close()


def test_floodwait_preserves_previous_data_and_cooldown(tmp_path,monkeypatch):
    store=Store(tmp_path)
    store.upsert(Chat(id='1',title='Сохранённый чат'))
    service=TelegramService(store)
    class FloodClient:
        async def iter_dialogs(self,**kwargs):
            raise errors.FloodWaitError(request=None,capture=60)
            yield
    service.get_client=AsyncMock(return_value=FloodClient())
    asyncio.run(service.scan())
    assert len(store.all())==1
    assert store.metadata('telegram_cooldown_until')
    assert '60' in service.status['error']
    assert not store.metadata('last_scan_at')
    store.close()


def test_folder_apply_preserves_unrelated_folders(tmp_path):
    store=Store(tmp_path)
    store.upsert(Chat(id='123',title='Закреплён',pinned=True,messages=[Message(id=1,date=utcnow(),outgoing=False,text='Тест')]))
    manual=types.DialogFilter(id=2,title=types.TextWithEntities('Личное',[]),pinned_peers=[],include_peers=[],exclude_peers=[])
    writes=[]
    class FakeClient:
        async def __call__(self,request):
            if isinstance(request,functions.messages.GetDialogFiltersRequest):
                return SimpleNamespace(filters=[manual])
            assert isinstance(request,functions.messages.UpdateDialogFilterRequest)
            writes.append(request)
            return True
        async def get_input_entity(self,chat_id):
            return types.InputPeerUser(chat_id,42)
    service=TelegramService(store)
    service.authorized=True
    service.get_client=AsyncMock(return_value=FakeClient())
    result=asyncio.run(service.apply_folders())
    assert result['updated']==['CRM Важные']
    assert len(writes)==1 and writes[0].id!=2
    assert writes[0].filter.include_peers[0].user_id==123
    store.close()


class HistoryClient:
    """Fake client holding one chat; honours min_id like messages.getHistory."""
    def __init__(self,texts):
        self.history=[types.Message(id=i,peer_id=types.PeerUser(7),date=utcnow(),message=t,out=False) for i,t in enumerate(texts,1)]
        self.calls=[]
        self.floods=[]
    def dialog(self):
        top=self.history[-1] if self.history else None
        return SimpleNamespace(id=7,name='Чат',entity=types.User(id=7,first_name='Чат'),message=top,
                               input_entity=types.InputPeerUser(7,1),is_user=True,is_group=False,is_channel=False,
                               pinned=False,archived=False,unread_count=0)
    async def iter_dialogs(self,**kwargs):
        yield self.dialog()
    async def iter_messages(self,entity,limit,min_id=0):
        self.calls.append(min_id)
        if self.floods:
            raise errors.FloodWaitError(request=None,capture=self.floods.pop(0))
        for message in reversed([m for m in self.history if m.id>min_id][-limit:]):
            yield message
    def add(self,text):
        self.history.append(types.Message(id=len(self.history)+1,peer_id=types.PeerUser(7),date=utcnow(),message=text,out=False))


def scanned(store):
    return [m.text for m in store.get('7')['chat'].messages]


def test_incremental_scan_fetches_only_new_messages(tmp_path,monkeypatch):
    monkeypatch.setattr('telegram_crm.telegram.asyncio.sleep',AsyncMock())
    store=Store(tmp_path); service=TelegramService(store)
    client=HistoryClient(['a','b']); service.get_client=AsyncMock(return_value=client)
    asyncio.run(service.scan())
    client.add('c'); client.add('d')
    asyncio.run(service.scan())
    assert client.calls==[0,2]  # second request only asks for messages newer than id 2
    assert scanned(store)==['d','c','b','a']
    first_fetch=store.get('7')['chat'].history_fetched_at
    asyncio.run(service.scan(full=True))
    assert client.calls==[0,2,0] and store.get('7')['chat'].history_fetched_at>first_fetch
    assert 'запрошено у Telegram: 1' in service.status['message']
    store.close()


def test_incremental_keeps_history_limit_and_weekly_full_reload(tmp_path,monkeypatch):
    monkeypatch.setattr('telegram_crm.telegram.asyncio.sleep',AsyncMock())
    store=Store(tmp_path); store.save_settings(Settings(history_limit=20)); service=TelegramService(store)
    client=HistoryClient([str(i) for i in range(20)]); service.get_client=AsyncMock(return_value=client)
    asyncio.run(service.scan())
    for i in range(5): client.add(f'new{i}')
    asyncio.run(service.scan())
    assert len(store.get('7')['chat'].messages)==20 and scanned(store)[0]=='new4'
    chat=store.get('7')['chat']
    store.upsert(chat.model_copy(update={'history_fetched_at':utcnow()-FULL_REFRESH_AFTER}))
    asyncio.run(service.scan())
    assert client.calls[-1]==0  # stale cache triggers a full reload even with no new messages
    store.close()


def test_deleted_newest_message_triggers_full_reload(tmp_path,monkeypatch):
    monkeypatch.setattr('telegram_crm.telegram.asyncio.sleep',AsyncMock())
    store=Store(tmp_path); service=TelegramService(store)
    client=HistoryClient(['a','b','c']); service.get_client=AsyncMock(return_value=client)
    asyncio.run(service.scan())
    client.history.pop()
    asyncio.run(service.scan())
    assert client.calls==[0,0] and scanned(store)==['b','a']
    store.close()


def test_short_floodwait_is_waited_out_with_backoff(tmp_path,monkeypatch):
    sleeps=[]
    async def fake_sleep(seconds): sleeps.append(seconds)
    monkeypatch.setattr('telegram_crm.telegram.asyncio.sleep',fake_sleep)
    store=Store(tmp_path); service=TelegramService(store)
    client=HistoryClient(['a']); client.floods=[5]
    service.get_client=AsyncMock(return_value=client)
    asyncio.run(service.scan())
    assert not service.status['error'] and scanned(store)==['a']
    assert sleeps[0]==6 and sleeps[1]>HISTORY_DELAY_MIN  # waited, then paced more slowly
    assert not store.metadata('telegram_cooldown_until')
    store.close()


def test_long_floodwait_still_stops_scan_with_cooldown(tmp_path,monkeypatch):
    monkeypatch.setattr('telegram_crm.telegram.asyncio.sleep',AsyncMock())
    store=Store(tmp_path); service=TelegramService(store)
    client=HistoryClient(['a']); client.floods=[600]
    service.get_client=AsyncMock(return_value=client)
    asyncio.run(service.scan())
    assert '600' in service.status['error'] and store.metadata('telegram_cooldown_until')
    store.close()
