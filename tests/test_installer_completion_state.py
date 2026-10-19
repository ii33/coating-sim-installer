import pytest

from tools.dependency_manager.completion_state import terminal_outcome


@pytest.mark.parametrize('operation,code,info,state,title,shortcut_retry', [
    ('install', 0, {'shortcut': {'status': 'success'}}, 'success', '安装完成', False),
    ('project-update', 0, {'shortcut': {'status': 'success'}, 'up_to_date': False}, 'success', '更新完成', False),
    ('project-update', 0, {'shortcut': {'status': 'success'}, 'up_to_date': True}, 'success', '已是最新版', False),
    ('shortcut', 0, {'shortcut': {'status': 'success', 'reused': False}}, 'success', '桌面快捷方式已就绪', False),
    ('install', 2, {}, 'failed', '操作失败', False),
    ('install', 130, {'shortcut': {'status': 'success'}}, 'cancelled', '已取消', False),
    # The desktop shortcut is noncritical: its failure keeps the install/update and
    # reports success-with-warning that offers only the dedicated shortcut retry.
    ('install', 0, {'shortcut': {'status': 'failed', 'errors': ['Desktop blocked']}}, 'warning', '桌面快捷方式待重试', True),
    ('project-update', 0, {'shortcut': {'status': 'failed', 'errors': ['Desktop blocked']}}, 'warning', '桌面快捷方式待重试', True),
    ('shortcut', 2, {'shortcut': {'status': 'failed', 'errors': ['Desktop blocked']}}, 'failed', '桌面快捷方式仍未完成', True),
    ('install', 0, {}, 'unconfirmed', '结果待确认', False),
])
def test_terminal_outcomes_do_not_conflate_completion_failure_and_cancel(operation, code, info, state, title, shortcut_retry):
    view = terminal_outcome(operation, code, info, 'access denied', requires_shortcut=True)
    assert view['state'] == state and title in view['title']
    assert view['code'] == code
    assert view.get('shortcut_retry', False) is shortcut_retry
    if state == 'warning':
        # A shortcut warning is still a successful operation with a concrete cause
        # and a dedicated retry that must not rerun install/update.
        assert view['code'] == 0 and view['retry'] is False and shortcut_retry is True
        assert 'Desktop blocked' in view['detail']
    if state == 'failed':
        if operation == 'shortcut':
            assert view['retry'] is False and shortcut_retry is True
        else:
            assert view['retry'] is True and view['code'] != 0
        assert 'denied' in view['detail'] or 'Desktop blocked' in view['detail']
    if state == 'cancelled':
        assert '完成' not in view['title'] and view['code'] == 130
