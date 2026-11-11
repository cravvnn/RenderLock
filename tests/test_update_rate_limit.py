import io
from email.message import Message
from urllib.error import HTTPError

import pytest

from agr import updater as u, update_ui
from test_updater import ui, release


def response(code=403, body=b'{"message":"API rate limit exceeded"}', **headers):
    values=Message()
    for name,value in headers.items():values[name.replace('_','-')]=value
    return HTTPError(u.RELEASES_URL,code,'Forbidden',values,io.BytesIO(body))


@pytest.mark.parametrize('headers,expected',[
    ({'X_RateLimit_Remaining':'0','X_RateLimit_Reset':'1000'},1000),
    ({'Retry_After':'120'},220),
    ({'Retry_After':'Thu, 01 Jan 1970 00:05:00 GMT'},300),
    ({'X_RateLimit_Reset':'past'},160),
    ({'X_RateLimit_Reset':'nan','Retry_After':'inf'},160),
    ({'X_RateLimit_Reset':'999999999999999'},160),
])
def test_limit_deadline_honors_headers_and_bounds_malformed_values(headers,expected):
    assert u.rate_limit_error(response(**headers),now=100).retry_at==expected


def test_secondary_limit_and_429_are_recognized_but_other_denials_are_not():
    assert u.rate_limit_error(response(body=b'{"message":"secondary rate limit"}'),now=100)
    assert u.rate_limit_error(response(code=429,body=b''),now=100)
    assert u.rate_limit_error(response(body=b'{"message":"Access denied"}'),now=100) is None
    assert u.rate_limit_error(response(code=404),now=100) is None


def test_check_wraps_rate_limit_and_closes_response_without_retry(monkeypatch):
    error=response(X_RateLimit_Reset='1000');calls=[]
    def denied(url):calls.append(url);raise error
    monkeypatch.setattr(u,'open_url',denied);monkeypatch.setattr(u.time,'time',lambda:100)
    with pytest.raises(u.UpdateRateLimited,match='internet connection') as caught:u.check_latest('0.3.0b26')
    assert caught.value.retry_at==1000 and calls==[u.RELEASES_URL] and error.closed


def test_other_http_failure_is_not_mislabeled(monkeypatch):
    error=response(body=b'Access denied')
    monkeypatch.setattr(u,'open_url',lambda url:(_ for _ in ()).throw(error))
    with pytest.raises(HTTPError) as caught:u.check_latest('0.3.0b26')
    assert caught.value is error and error.closed


def test_rate_limit_blocks_checks_until_deadline_but_keeps_manual_browser_route(tmp_path,monkeypatch):
    now=[100];monkeypatch.setattr(update_ui.time,'time',lambda:now[0])
    app,controller=ui(tmp_path,monkeypatch);runs=[];opened=[]
    monkeypatch.setattr(controller,'_run',lambda *args:runs.append(args))
    monkeypatch.setattr(update_ui.webbrowser,'open',opened.append)
    controller.events.put((0,'rate_limited',u.UpdateRateLimited(200)));controller.poll()
    assert app.update_check.options['state']=='disabled'
    assert app.update_action.options['state']=='disabled'
    assert app.update_notes_link.options['state']=='normal'
    controller.check();assert not runs
    controller.open_notes();assert opened==[u.RELEASES_PAGE]
    assert 'unchanged' in app.update_status.value
    now[0]=200;controller.poll();assert app.update_check.options['state']=='normal'
    assert not runs # Expiration re-enables a user check, never an automatic retry.
    controller.check();assert len(runs)==1


def test_stale_limit_does_not_change_state_or_discard_verified_download(tmp_path,monkeypatch):
    monkeypatch.setattr(update_ui.time,'time',lambda:100)
    app,controller=ui(tmp_path,monkeypatch);controller.request=2
    controller.available=u.select_release([release()],'0.3.0b4');controller.ready=tmp_path
    controller.events.put((1,'rate_limited',u.UpdateRateLimited(200)));controller.poll()
    assert controller.retry_at==0 and controller.ready==tmp_path
    controller.events.put((2,'rate_limited',u.UpdateRateLimited(200)));controller.poll()
    assert controller.ready==tmp_path and app.update_action.options['text']=='Install and restart'
    assert app.update_action.options['state']=='normal'
