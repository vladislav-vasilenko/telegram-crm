import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from telegram_crm.models import Chat, Message
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


def test_local_security_checks(client):
    assert client.post('/api/demo',headers={'X-CRM-Token':'wrong'}).status_code==403
    assert client.post('/api/demo',headers={'Origin':'https://example.com'}).status_code==403
    assert client.get('/api/state',headers={'Host':'evil.example'}).status_code==403


def test_demo_is_synthetic_and_has_all_requested_categories(client):
    state=seed(client)
    assert state['demo']
    assert len(state['chats'])==12
    for category in ['important','stalled','suspected_scam','inactive','very_old','dead']:
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
