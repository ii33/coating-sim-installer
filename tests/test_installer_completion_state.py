import pytest

from tools.dependency_manager.completion_state import terminal_outcome


@pytest.mark.parametrize('operation,code,info,state,title', [
    ('install', 0, {'shortcut': {'status': 'success'}}, 'success', '安装完成'),
    ('project-update', 0, {'shortcut': {'status': 'success'}, 'up_to_date': False}, 'success', '更新完成'),
    ('project-update', 0, {'shortcut': {'status': 'success'}, 'up_to_date': True}, 'success', '已是最新版'),
    ('install', 2, {}, 'failed', '操作失败'),
    ('install', 130, {'shortcut': {'status': 'success'}}, 'cancelled', '已取消'),
    ('install', 0, {'shortcut': {'status': 'failed', 'errors': ['Desktop blocked']}}, 'failed', '操作失败'),
    ('install', 0, {}, 'unconfirmed', '结果待确认'),
])
def test_terminal_outcomes_do_not_conflate_completion_failure_and_cancel(operation, code, info, state, title):
    view = terminal_outcome(operation, code, info, 'access denied', requires_shortcut=True)
    assert view['state'] == state and title in view['title']
    if state == 'failed':
        assert view['retry'] is True and view['code'] != 0
        assert 'denied' in view['detail'] or 'Desktop blocked' in view['detail']
    if state == 'cancelled':
        assert '完成' not in view['title'] and view['code'] == 130
