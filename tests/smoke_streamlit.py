"""Run explicitly: python -B tests/smoke_streamlit.py (requires working jieba)."""
from pathlib import Path
from streamlit.testing.v1 import AppTest


def main():
    app = AppTest.from_file(str(Path(__file__).absolute().parents[1] / 'app.py'),
                            default_timeout=60).run()
    assert not app.exception, [e.message for e in app.exception]
    app.text_area(key='draft').set_value('明華：今天上課。\n月美：你好。')
    next(b for b in app.button if b.label == '執行 Python 檢測').click().run()
    assert not app.exception, [e.message for e in app.exception]
    approve = next(b for b in app.button if b.label == '將目前檢測內容確認為定稿')
    assert not approve.disabled
    approve.click().run()
    assert not app.exception, [e.message for e in app.exception]
    assert app.session_state['approved']
    assert any('single_choice' in code.value for code in app.code)
    print('PASS: initial render -> audit -> approve -> stage three prompt')


if __name__ == '__main__':
    main()
