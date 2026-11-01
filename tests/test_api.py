import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from telegram_crm.models import Chat, Message, Settings
from telegram_crm.server import create_app
from telegram_crm.store import utcnow


@pytest.fixture
def client(tmp_path):
    app=create_app(tmp_path,testing=True)
    with TestClient(app) as client:
        client.headers["X-CRM-Token"]=client.get('/api/state').json()['token']
        yield client


def seed(client):
    assert client.post('/api/demo').status_code==200
    return client.get('/api/state').json()


def answer(chat_id="chat_1", evidence=None):
    return {"results":[{"chat_id":chat_id,"importance":"normal","dating":"yes","development":"stalled","scam_risk":"low","confidence":0.9,"reason":"Во фрагменте есть контекст знакомства, развитие не подтверждено.","evidence_message_ids":evidence or [],"suggested_action":"Проверить контекст вручную"}]}


def test_empty_app_and_static_ui(client):
    assert client.get('/').status_code==200
    assert client.get('/api/state').json()['chats']==[]
    assert client.get('/api/health').json()['application']=='telegram-dialogue-crm'
    assert 'frame-ancestors' in client.get('/').headers['content-security-policy']
    view=client.get('/telegram')
    assert view.status_code==200 and 'telegram-view.js' in view.text and 'frame-ancestors' in view.headers['content-security-policy']


def test_local_security_checks(client):
    assert client.post('/api/demo',headers={'X-CRM-Token':'wrong'}).status_code==403
    assert client.post('/api/demo',headers={'Origin':'https://example.com'}).status_code==403
    assert client.get('/api/state',headers={'Host':'evil.example'}).status_code==403


def test_demo_is_synthetic_and_has_all_requested_categories(client):
    state=seed(client)
    assert state['demo']
    assert len(state['chats'])==13
    for category in ['important','job','stalled','suspected_scam','inactive','very_old','dead']:
        assert state['counts'][category]>0


def test_annotation_persists_and_applies(client):
    seed(client)
    annotation={'important':True,'dating':False,'notes':'Важный контакт','reply_draft':'Спасибо!'}
    assert client.put('/api/chats/demo_3/annotation',json=annotation).status_code==200
    row=client.get('/api/chats/demo_3').json()
    assert 'important' in row['categories'] and 'stalled' not in row['categories']
    assert row['annotation']['reply_draft']=='Спасибо!'


def test_batch_exports_twenty_in_chronological_order_without_names(client):
    state=seed(client)
    client.app.state.store.save_settings(Settings(llm_messages_per_chat=20))
    exported=client.post('/api/batches/export',json={'chat_ids':['demo_1','demo_3']}).json()
    assert exported['message_counts']==[20,20]
    data=client.app.state.store.batch(exported['batch_id'])['payload']
    assert data['chats'][0]['title']=='Чат 1'
    assert [m['id'] for m in data['chats'][0]['messages']]==list(range(27,47))
    assert 'Алина' not in exported['prompt']
    assert data['chats'][0]['chat_id']=='chat_1'


def test_import_validated_and_cannot_overwrite_manual_labels(client):
    seed(client)
    client.put('/api/chats/demo_3/annotation',json={'dating':False})
    batch=client.post('/api/batches/export',json={'chat_ids':['demo_3']}).json()
    response=client.post('/api/batches/import',json={'batch_id':batch['batch_id'],'result':answer(evidence=[20])})
    assert response.json()['imported']==1
    row=client.get('/api/chats/demo_3').json()
    assert row['llm'] and 'stalled' not in row['categories']


@pytest.mark.parametrize('value',[answer('unknown'),answer(evidence=[999999])])
def test_import_rejects_unknown_chats_and_hallucinated_evidence(client,value):
    seed(client)
    batch=client.post('/api/batches/export',json={'chat_ids':['demo_3']}).json()
    assert client.post('/api/batches/import',json={'batch_id':batch['batch_id'],'result':value}).status_code==400
    assert client.get('/api/chats/demo_3').json()['llm'] is None


def test_import_rejects_changed_history(client):
    seed(client)
    batch=client.post('/api/batches/export',json={'chat_ids':['demo_3']}).json()
    store=client.app.state.store
    chat=store.get('demo_3')['chat']
    chat.messages.append(Message(id=999,date=utcnow(),outgoing=False,text='Новое сообщение'))
    store.upsert(chat)
    assert client.post('/api/batches/import',json={'batch_id':batch['batch_id'],'result':answer()}).status_code==400


def test_contact_redaction_and_short_history(client):
    store=client.app.state.store
    store.upsert(Chat(id='10',title='Реальное имя',messages=[Message(id=1,date=utcnow(),outgoing=False,text='Мне на +7 999 123-45-67, test@example.com и @private_name')]))
    result=client.post('/api/batches/export',json={'chat_ids':['10']}).json()
    assert result['message_counts']==[1]
    assert '+7 999' not in result['prompt'] and 'test@example.com' not in result['prompt'] and '@private_name' not in result['prompt']


def test_bad_inputs_do_not_echo_secrets(client):
    secret='not-a-valid-secret'
    response=client.post('/api/auth/start',json={'api_id':123,'api_hash':secret,'phone':'+79991234567'})
    assert response.status_code==422 and secret not in response.text
    assert client.put('/api/settings',json={'history_limit':10}).status_code==422
    assert client.post('/api/scan').status_code==400


def test_key_not_exposed_and_llm_not_called_implicitly(client):
    seed(client)
    assert client.put('/api/llm/key',json={'api_key':'sk-fake-for-testing-only'}).status_code==200
    response=client.get('/api/state')
    assert response.json()['llm_configured']
    assert 'sk-fake' not in response.text
    assert not response.json()['llm_job']['running']


def test_demo_does_not_overwrite_real_data(client):
    client.app.state.store.upsert(Chat(id='1',title='Личный чат'))
    assert client.post('/api/demo').status_code==400
    assert len(client.get('/api/state').json()['chats'])==1


def test_csv_export_preserves_zero_and_defuses_formulas(client):
    seed(client)
    store=client.app.state.store
    store.upsert(Chat(id='1',title='=HYPERLINK("evil")'))
    response=client.get('/api/export.csv')
    assert response.status_code==200
    assert "'=HYPERLINK" in response.text


def test_demo_folders_never_contain_synthetic_chats(client):
    seed(client)
    assert all(not folder['chat_ids'] for folder in client.get('/api/folders/plan').json()['folders'])


def test_qr_token_not_exposed_in_global_state_or_without_csrf(client):
    client.app.state.telegram.qr_image='data:image/png;base64,PRIVATE_TEST_IMAGE'
    assert 'PRIVATE_TEST_IMAGE' not in client.get('/api/state').text
    assert client.get('/api/auth/qr/status',headers={'X-CRM-Token':'wrong'}).status_code==403
    assert 'PRIVATE_TEST_IMAGE' in client.get('/api/auth/qr/status').text


def test_cleanup_protects_important_and_filters_by_action(client, monkeypatch):
    store, telegram = client.app.state.store, client.app.state.telegram
    now = utcnow()
    store.upsert(Chat(id='1', title='Закреплённый', pinned=True, messages=[Message(id=1, date=now, outgoing=False, text='x')]))
    store.upsert(Chat(id='2', title='Человек', messages=[Message(id=1, date=now, outgoing=False, text='x')]))
    store.upsert(Chat(id='-3', title='Канал', kind='channel', messages=[Message(id=1, date=now, outgoing=False, text='x')]))
    plans = []
    monkeypatch.setattr(telegram, 'start_cleanup', lambda plan: plans.append(plan))
    response = client.post('/api/cleanup', json={'chat_ids': ['1', '2', '-3', 'nope'], 'unfollow': True})
    assert response.status_code == 202
    assert [p['id'] for p in plans[0]] == ['-3'] and plans[0][0]['action'] == 'leave'
    assert response.json()['skipped'] == {'important': 1, 'not_selected_action': 1, 'unknown': 1}
    response = client.post('/api/cleanup', json={'chat_ids': ['1', '2'], 'delete': True, 'include_important': True})
    assert [(p['id'], p['action']) for p in plans[1]] == [('1', 'delete'), ('2', 'delete')]
    assert client.post('/api/cleanup', json={'chat_ids': ['2']}).status_code == 422  # no action chosen
    assert client.post('/api/cleanup', json={'chat_ids': ['1'], 'delete': True}).status_code == 400  # only important


def test_llm_window_estimate_and_copy_paste_cap(client, monkeypatch):
    seed(client)
    store = client.app.state.store
    assert client.post('/api/batches/export', json={'chat_ids': ['demo_1']}).json()['message_counts'][0] > 20
    estimate = client.post('/api/llm/estimate', json={'chat_ids': ['demo_1', 'demo_3']}).json()
    assert estimate['provider'] == 'openai' and estimate['requests'] == 1 and estimate['cost_usd'] is None
    store.save_settings(Settings(llm_model='claude-haiku-5-5'))
    claude = client.post('/api/llm/estimate', json={'chat_ids': ['demo_1']}).json()
    assert claude['provider'] == 'anthropic' and claude['cost_usd'] is not None  # built-in Haiku 5.5 price
    state = client.get('/api/state').json()
    assert state['llm_provider'] == 'anthropic' and state['llm_keys'] == {'openai': False, 'anthropic': False}
    assert client.put('/api/llm/key', json={'api_key': 'sk-ant-fake-for-tests', 'provider': 'anthropic'}).status_code == 200
    assert client.get('/api/state').json()['llm_keys']['anthropic'] and 'sk-ant-fake' not in client.get('/api/state').text
    monkeypatch.setattr('telegram_crm.llm.COPY_PASTE_MAX_TOKENS', 10)
    response = client.post('/api/batches/export', json={'chat_ids': ['demo_1']})
    assert response.status_code == 400 and 'API' in response.json()['detail']


def _fake_sdks(monkeypatch, decide):
    """Replace both SDK modules; decide(call_no, aliases) -> list of aliases to answer, or None to fail."""
    import asyncio, json as _json, sys, types as pytypes
    from telegram_crm.models import LLMBatchResult
    calls = {'n': 0, 'active': 0, 'peak': 0, 'system_in_user': False}

    async def respond(system, user):
        calls['n'] += 1; n = calls['n']; calls['active'] += 1; calls['peak'] = max(calls['peak'], calls['active'])
        await asyncio.sleep(0.01); calls['active'] -= 1
        calls['system_in_user'] |= system[:40] in user
        aliases = [c['chat_id'] for c in _json.loads(user.split('Данные:\n', 1)[1])['chats']]
        answer_for = decide(n, aliases)
        return None if answer_for is None else LLMBatchResult.model_validate({'results': [answer(a)['results'][0] for a in answer_for]})

    class Ctx:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False

    class OpenAIClient(Ctx):
        class responses:
            @staticmethod
            async def parse(**kw):
                return pytypes.SimpleNamespace(output_parsed=await respond(kw['input'][0]['content'], kw['input'][1]['content']))

    class AnthropicClient(Ctx):
        class messages:
            @staticmethod
            async def parse(**kw):
                parsed = await respond(kw['system'], kw['messages'][0]['content'])
                return pytypes.SimpleNamespace(stop_reason='refusal' if parsed is None else 'end_turn', parsed_output=parsed)

    monkeypatch.setitem(sys.modules, 'openai', pytypes.SimpleNamespace(AsyncOpenAI=OpenAIClient))
    monkeypatch.setitem(sys.modules, 'anthropic', pytypes.SimpleNamespace(AsyncAnthropic=AnthropicClient))
    return calls


@pytest.mark.parametrize('model', ['gpt-6-luna', 'claude-haiku-5-5'])
def test_analyze_parts_run_in_parallel_and_failed_part_is_discarded(tmp_path, monkeypatch, model):
    import asyncio
    from telegram_crm import llm
    from telegram_crm.demo import load_demo
    from telegram_crm.models import BatchRequest
    from telegram_crm.store import Store
    store = Store(tmp_path); load_demo(store)
    ids = [row['chat'].id for row in store.all()]
    monkeypatch.setattr(llm, 'CHUNK_SIZE', 2)
    calls = _fake_sdks(monkeypatch, lambda n, aliases: None if n == 1 else aliases)  # first part refused
    with pytest.raises(llm.PartsFailed, match='Не сохранено частей: 1'):
        asyncio.run(llm.analyze_batch(store, BatchRequest(chat_ids=ids), 'key', model))
    assert 1 < calls['peak'] <= llm.PARALLEL and not calls['system_in_user']
    rows = [row for row in store.all() if row['llm']]
    assert len(rows) == len(ids) - 2
    assert all(row['llm']['source'].startswith('Anthropic' if model.startswith('claude') else 'OpenAI') for row in rows)
    store.close()


def test_incomplete_part_is_not_saved(tmp_path, monkeypatch):
    import asyncio
    from telegram_crm import llm
    from telegram_crm.demo import load_demo
    from telegram_crm.models import BatchRequest
    from telegram_crm.store import Store
    store = Store(tmp_path); load_demo(store)
    ids = [row['chat'].id for row in store.all()][:2]
    _fake_sdks(monkeypatch, lambda n, aliases: aliases[:1])  # model skips a chat
    with pytest.raises(llm.PartsFailed):
        asyncio.run(llm.analyze_batch(store, BatchRequest(chat_ids=ids), 'key', 'claude-haiku-5-5'))
    assert not any(row['llm'] for row in store.all())
    store.close()


def test_search_finds_older_messages_with_snippet(client):
    seed(client)
    messages=[Message(id=1,date=utcnow()-timedelta(days=2),outgoing=False,text='Прислали ТЕСТОВОЕ задание вчера'),
              Message(id=2,date=utcnow()-timedelta(days=1),outgoing=True,text='Понятно')]
    client.app.state.store.upsert(Chat(id='777',title='Поиск',messages=messages,demo=True))
    result=client.get('/api/search',params={'q':'тестовое'}).json()
    assert list(result['hits'])==['777'] and 'ТЕСТОВОЕ задание' in result['hits']['777']
    assert client.get('/api/search',params={'q':'т'}).json()['hits']=={}
    assert client.get('/api/search',params={'q':'(.*'}).json()['hits']=={}


def test_triage_marks_changed_chats_for_review(client):
    from telegram_crm.triage import build_triage
    store=client.app.state.store
    old=utcnow()-timedelta(days=500)
    store.upsert(Chat(id='11',title='Холодный',last_message_id=1,messages=[Message(id=1,date=old,outgoing=False,text='Здравствуйте')]))
    build_triage(store)
    state=client.get('/api/state').json()
    assert state['chats'][0]['triage']['color']=='red' and state['chats'][0]['triage']['bucket']=='remove_cold' and state['triage']['buckets']['remove_cold']['count']==1
    new=[Message(id=1,date=old,outgoing=False,text='Здравствуйте'),Message(id=2,date=utcnow(),outgoing=False,text='Вы тут?')]
    store.upsert(Chat(id='11',title='Холодный',last_message_id=2,messages=new))
    assert client.get('/api/state').json()['chats'][0]['triage']['bucket']=='review_changed'


@pytest.mark.parametrize("stage,sent", [("kissed","met"),("sex_protected","met"),("sex_unprotected","met"),("regular_partner","relationship"),("talking","talking")])
def test_intimate_stages_are_saved_but_generalised_for_llm(client,stage,sent):
    seed(client)
    assert client.put('/api/chats/demo_1/annotation',json={'dating':True,'stage':stage}).status_code==200
    assert client.get('/api/chats/demo_1').json()['annotation']['stage']==stage
    exported=client.post('/api/batches/export',json={'chat_ids':['demo_1']}).json()
    assert f'"manual_stage": "{sent}"' in exported['prompt']
    assert stage not in ('sex_protected','sex_unprotected','regular_partner','kissed') or stage not in exported['prompt']
