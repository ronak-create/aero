"""TUI tests: block rendering, the input editor, approvals, and message queueing.

These drive the app object directly instead of taking over a real terminal,
so `app.out` is redirected to a throwaway buffer.
"""
import io
import threading
import time
import unittest
from dataclasses import dataclass

from term import KeyEvent, strip_ansi, text_width
from tui import (
    AssistantBlock, BannerBlock, DiffBlock, InputEditor, ReasoningBlock,
    SystemBlock, TuiApp, TuiCallbacks, ToolBlock, UserBlock, _fmt_tokens,
)


@dataclass
class FakeConfig:
    model: str = "Atria-Dawn-Preview"
    base_url: str = "https://api.atria-asi.ai/v1"
    auto_approve: bool = False
    max_iterations: int = 25
    request_timeout: int = 120
    temperature: float = 0.2


class FakeClient:
    """Minimal stand-in: LLMClient is only touched when a turn actually runs."""

    def list_models(self):
        return ["Atria-Dawn-Preview"]

    def stream(self, messages, tools=None, temperature=0.2):
        """An empty stream, so a dispatched turn ends cleanly with no network."""
        yield {"reasoning": None, "content": None, "tool_calls": []}


def make_app(**kwargs) -> TuiApp:
    config = FakeConfig(**kwargs)
    app = TuiApp(FakeClient(), config)
    app.out = io.StringIO()
    return app


class TestBlocks(unittest.TestCase):
    def test_user_block_wraps_and_indents(self):
        lines = UserBlock(text="one two three four five").render(20)
        joined = [strip_ansi(line) for line in lines]
        # A green dot marks the user's turn, not a "you" label.
        self.assertTrue(joined[0].startswith("● "))
        self.assertNotIn("you", joined[0])
        # Continuation lines align under the message body, past the marker.
        for line in joined[1:]:
            if line:
                self.assertTrue(line.startswith("  "))

    def test_user_block_empty_when_no_text(self):
        self.assertEqual(UserBlock(text="").render(40), [])

    def test_reasoning_collapses_to_one_line(self):
        block = ReasoningBlock(text="first\nsecond\nthird" * 8)
        lines = [strip_ansi(line) for line in block.render(60)]
        self.assertEqual(len(lines), 2)  # one line + blank
        self.assertIn("reasoning", lines[0])
        # Collapsed view truncates rather than showing the whole monologue.
        self.assertIn("…", lines[0])

    def test_reasoning_collapsed_short_text_fits(self):
        block = ReasoningBlock(text="just a short thought")
        lines = [strip_ansi(line) for line in block.render(60)]
        self.assertEqual(len(lines), 2)
        self.assertNotIn("…", lines[0])

    def test_reasoning_expanded_shows_everything(self):
        block = ReasoningBlock(text="first\nsecond", collapsed=False)
        lines = [strip_ansi(line) for line in block.render(60)]
        self.assertTrue(any("first" in line for line in lines))
        self.assertTrue(any("second" in line for line in lines))

    def test_reasoning_empty_renders_nothing(self):
        self.assertEqual(ReasoningBlock(text="").render(60), [])

    def test_assistant_renders_markdown(self):
        lines = [strip_ansi(line) for line in AssistantBlock(text="# Hi").render(60)]
        # A ">" marker leads the first line; the heading follows it.
        self.assertTrue(lines[0].startswith("> "))
        self.assertTrue(lines[0].endswith("Hi"))

    def test_assistant_placeholder_while_thinking(self):
        lines = [strip_ansi(line) for line in AssistantBlock().render(60)]
        self.assertIn("thinking", lines[0])

    def test_assistant_done_with_no_output(self):
        block = AssistantBlock(done=True)
        lines = [strip_ansi(line) for line in block.render(60)]
        self.assertIn("no output", lines[0])

    def test_system_block_colors_by_kind(self):
        for kind in ("info", "warn", "error"):
            lines = SystemBlock(text="a message", kind=kind).render(60)
            self.assertEqual(strip_ansi(lines[0]), "a message")

    def test_tool_block_status_icons(self):
        for status in ("running", "done", "error", "denied"):
            block = ToolBlock(name="read_file", args={"path": "a.py"},
                              status=status)
            lines = block.render(60)
            self.assertEqual(len(lines), 1)

    def test_tool_block_summaries(self):
        self.assertIn("ls -la", strip_ansi(
            ToolBlock(name="run_shell", args={"command": "ls -la"}).render(60)[0]))
        self.assertIn("src/a.py", strip_ansi(
            ToolBlock(name="read_file", args={"path": "src/a.py"}).render(60)[0]))

    def test_tool_block_expanded_shows_result(self):
        block = ToolBlock(name="run_shell", args={"command": "echo hi"},
                          status="done", result="exit_code: 0\nstdout:\nhi",
                          expanded=True)
        lines = [strip_ansi(line) for line in block.render(60)]
        self.assertTrue(any("exit_code: 0" in line for line in lines))

    def test_banner_renders_stats(self):
        banner = BannerBlock(model="Atria-Dawn-Preview",
                             base_url="https://api.atria-asi.ai/v1",
                             cwd="/some/path", tool_count=11,
                             plugin_names=("weather",), yolo=True)
        lines = [strip_ansi(line) for line in banner.render(80)]
        joined = "\n".join(lines)
        self.assertIn("Atria-Dawn-Preview", joined)
        self.assertIn("api.atria-asi.ai", joined)
        self.assertIn("weather", joined)
        self.assertIn("yolo", joined)

    def test_banner_long_cwd_is_abbreviated(self):
        long_cwd = "/a/really/quite/deeply/nested/project/directory/path"
        banner = BannerBlock(model="m", base_url="https://x.test/v1", cwd=long_cwd)
        for line in [strip_ansi(line) for line in banner.render(80)]:
            self.assertLessEqual(len(line), 81)

    def test_banner_stacks_when_narrow(self):
        banner = BannerBlock(model="Atria-Dawn-Preview",
                             base_url="https://api.atria-asi.ai/v1", cwd="/p")
        # Should not raise or produce lines wider than the terminal.
        for line in [strip_ansi(line) for line in banner.render(40)]:
            self.assertLessEqual(len(line), 42)

    def test_diff_block_pending_shows_prompt(self):
        block = DiffBlock(path="a.py", diff_lines=["@@ -1 +1 @@", "-old", "+new"])
        lines = [strip_ansi(line) for line in block.render(60)]
        joined = "\n".join(lines)
        self.assertIn("approve this change?", joined)
        self.assertIn("+new", joined)

    def test_diff_block_decided_states(self):
        for decided, marker in (("approved", "edited"), ("rejected", "rejected")):
            block = DiffBlock(path="a.py", decided=decided)
            joined = "\n".join(
                strip_ansi(line) for line in block.render(60))
            self.assertIn(marker, joined)


class TestInputEditor(unittest.TestCase):
    def test_insert_and_text(self):
        ed = InputEditor()
        ed.insert("hello")
        self.assertEqual(ed.text, "hello")

    def test_newline_splits(self):
        ed = InputEditor()
        ed.insert("ab")
        ed.col = 1
        ed.newline()
        self.assertEqual(ed.text, "a\nb")

    def test_backspace_at_start_of_line_merges_up(self):
        ed = InputEditor()
        ed.insert("ab")
        ed.newline()
        ed.insert("c")
        ed.backspace()  # deletes 'c'
        ed.backspace()  # merges the lines
        self.assertEqual(ed.text, "ab")

    def test_backspace_does_not_delete_past_start(self):
        ed = InputEditor()
        ed.backspace()
        self.assertEqual(ed.text, "")

    def test_delete_forward(self):
        ed = InputEditor()
        ed.insert("abc")
        ed.col = 0
        ed.delete()
        self.assertEqual(ed.text, "bc")

    def test_delete_across_line_boundary(self):
        ed = InputEditor()
        ed.insert("ab")
        ed.newline()
        ed.insert("c")
        ed.row, ed.col = 0, 2
        ed.delete()
        self.assertEqual(ed.text, "abc")

    def test_movement(self):
        ed = InputEditor()
        ed.insert("abc")
        ed.col = 0
        ed.move_right()
        self.assertEqual(ed.col, 1)
        ed.move_left()
        self.assertEqual(ed.col, 0)

    def test_up_down_navigate_lines(self):
        ed = InputEditor()
        ed.insert("one")
        ed.newline()
        ed.insert("two")
        self.assertTrue(ed.move_up())
        self.assertEqual(ed.row, 0)
        self.assertFalse(ed.move_up())  # at the top: caller treats as history
        self.assertTrue(ed.move_down())
        self.assertEqual(ed.row, 1)
        self.assertFalse(ed.move_down())

    def test_home_and_end(self):
        ed = InputEditor()
        ed.insert("abc")
        ed.home()
        self.assertEqual(ed.col, 0)
        ed.end()
        self.assertEqual(ed.col, 3)

    def test_kill(self):
        ed = InputEditor()
        ed.insert("abc def")
        ed.col = 3
        ed.kill_to_end()
        self.assertEqual(ed.text, "abc")
        ed.kill_to_start()
        self.assertEqual(ed.text, "")

    def test_delete_word_back(self):
        ed = InputEditor()
        ed.insert("hello world")
        ed.delete_word_back()
        self.assertEqual(ed.text, "hello ")
        # A second press takes the next word, including its trailing space.
        ed.delete_word_back()
        self.assertEqual(ed.text, "")

    def test_delete_word_back_mid_word(self):
        # Caret inside a word erases back to the word's start, splitting it.
        ed = InputEditor()
        ed.insert("hello world")
        ed.col = 8  # "hello wo|rld"
        ed.delete_word_back()
        self.assertEqual(ed.text, "hello rld")

    def test_delete_word_back_at_line_start_merges_up(self):
        # Caret at column 0 of a non-first line merges up, like backspace.
        ed = InputEditor()
        ed.insert("one")
        ed.newline()
        ed.insert("two")
        ed.col = 0  # this is what makes it a merge, not a word erase
        ed.delete_word_back()
        self.assertEqual(ed.text, "onetwo")

    def test_delete_word_back_in_middle_of_text(self):
        ed = InputEditor()
        ed.insert("one two three")
        ed.col = 8  # "one two |three"
        ed.delete_word_back()
        self.assertEqual(ed.text, "one two three"[0:4] + "three")
        self.assertEqual(ed.col, 4)

    def test_delete_word_back_at_very_start_is_a_noop(self):
        ed = InputEditor()
        ed.delete_word_back()
        self.assertEqual(ed.text, "")

    def test_delete_word_back_resets_history_index(self):
        ed = InputEditor()
        ed.history = ["older"]
        ed.hist_index = 0
        ed.insert("abc def")
        ed.delete_word_back()
        self.assertEqual(ed.hist_index, -1)

    def test_history_walks_and_restores_draft(self):
        ed = InputEditor()
        ed.history = ["first", "second"]
        ed.insert("current draft")
        ed.history_up()   # most recent first
        self.assertEqual(ed.text, "second")
        ed.history_up()   # then older
        self.assertEqual(ed.text, "first")
        ed.history_down()
        self.assertEqual(ed.text, "second")
        ed.history_down()  # past the end: the draft comes back
        self.assertEqual(ed.text, "current draft")

    def test_history_noop_when_empty(self):
        ed = InputEditor()
        ed.history_up()
        ed.history_down()
        self.assertEqual(ed.text, "")

    def test_submit_records_history_and_resets(self):
        ed = InputEditor()
        ed.insert("a real message")
        text = ed.submit()
        self.assertEqual(text, "a real message")
        self.assertEqual(ed.history, ["a real message"])
        self.assertEqual(ed.text, "")

    def test_submit_blank_does_not_record(self):
        ed = InputEditor()
        ed.insert("   ")
        ed.submit()
        self.assertEqual(ed.history, [])


class TestCallbackContract(unittest.TestCase):
    """The TUI callbacks must match what agent.py calls."""

    def test_tool_start_result_take_call_id(self):
        app = make_app()
        cb = TuiCallbacks(app)
        cb.on_tool_start("read_file", {"path": "a.py"}, "call-1")
        cb.on_tool_result("read_file", "OK", False, "call-1")
        self.assertEqual(app.tool_blocks[0].call_id, "call-1")
        self.assertEqual(app.tool_blocks[0].status, "done")

    def test_concurrent_same_tool_resolves_to_the_right_block(self):
        # Two read_file calls in flight: results must land on the matching
        # block, which is why blocks are keyed by call id.
        app = make_app()
        cb = TuiCallbacks(app)
        cb.on_tool_start("read_file", {"path": "a.py"}, "call-A")
        cb.on_tool_start("read_file", {"path": "b.py"}, "call-B")
        cb.on_tool_result("read_file", "OK b", False, "call-B")
        cb.on_tool_result("read_file", "OK a", False, "call-A")
        self.assertEqual(app.tool_blocks[0].result, "OK a")
        self.assertEqual(app.tool_blocks[1].result, "OK b")

    def test_result_without_call_id_falls_back_to_name(self):
        app = make_app()
        cb = TuiCallbacks(app)
        cb.on_tool_start("todo_read", {})  # no call id
        cb.on_tool_result("todo_read", "(empty)", False)
        self.assertEqual(app.tool_blocks[0].status, "done")

    def test_usage_accumulates_across_turns(self):
        app = make_app()
        cb = TuiCallbacks(app)
        cb.on_usage({"total_tokens": 10, "prompt_tokens": 4, "completion_tokens": 6})
        cb.on_usage({"total_tokens": 5, "prompt_tokens": 2, "completion_tokens": 3})
        self.assertEqual(app.last_usage["total_tokens"], 15)

    def test_content_and_reasoning_create_blocks(self):
        app = make_app()
        cb = TuiCallbacks(app)
        cb.on_reasoning_delta("thinking")
        cb.on_content_delta("answer")
        # The session banner is block[0]; the callbacks added the rest.
        added = app.blocks[1:]
        self.assertEqual(len(added), 2)
        self.assertIsInstance(added[0], ReasoningBlock)
        self.assertIsInstance(added[1], AssistantBlock)

    def test_warning_creates_block(self):
        app = make_app()
        cb = TuiCallbacks(app)
        cb.on_warning("something odd")
        added = app.blocks[1:]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].text, "something odd")


class TestApprovals(unittest.TestCase):
    def test_auto_approve_returns_true_and_logs(self):
        app = make_app(auto_approve=True)
        self.assertTrue(app.request_approval("run_shell: echo hi"))
        self.assertIn("auto-approved", app.blocks[-1].text)

    def test_answered_approval_returns_result(self):
        app = make_app()
        result = {}

        def ask():
            result["ok"] = app.request_approval("run_shell: echo hi")

        thread = threading.Thread(target=ask)
        thread.start()
        # Wait for the prompt to actually be pending before answering.
        deadline = time.time() + 2
        while time.time() < deadline:
            with app._approval_lock:
                if app._pending_approval is not None:
                    break
            time.sleep(0.01)
        self.assertIsNotNone(app._pending_approval)
        app._answer_approval(True)
        thread.join(timeout=2)
        self.assertTrue(result["ok"])
        self.assertIn("approved", app.blocks[-1].text)
        self.assertIsNone(app._pending_approval)

    def test_denied_approval(self):
        app = make_app()
        result = {}

        def ask():
            result["ok"] = app.request_approval("run_shell: echo hi")

        thread = threading.Thread(target=ask)
        thread.start()
        deadline = time.time() + 2
        while time.time() < deadline:
            with app._approval_lock:
                if app._pending_approval is not None:
                    break
            time.sleep(0.01)
        app._answer_approval(False)
        thread.join(timeout=2)
        self.assertFalse(result["ok"])
        self.assertIn("denied", app.blocks[-1].text)

    def test_interrupt_denies_without_crashing(self):
        # Regression: this path used to reference an undefined name and raise
        # NameError in the worker thread instead of denying cleanly.
        app = make_app()
        app._interrupt.set()
        self.assertFalse(app.request_approval("run_shell: echo hi"))
        self.assertIsNone(app._pending_approval)

    def test_quit_while_pending_denies(self):
        app = make_app()
        app.running = False
        self.assertFalse(app.request_approval("run_shell: echo hi"))

    def test_key_press_answers_pending_approval(self):
        app = make_app()
        event = threading.Event()
        with app._approval_lock:
            app._pending_approval = {
                "description": "run_shell: echo hi",
                "event": event,
                "result": False,
            }
        self.assertTrue(app.handle_key(KeyEvent(ch="y")))
        self.assertTrue(event.is_set())
        with app._approval_lock:
            self.assertTrue(app._pending_approval["result"])


class TestMessageQueue(unittest.TestCase):
    def test_input_while_busy_is_queued_not_dispatched(self):
        app = make_app()
        app.set_status("thinking")
        app.input.insert("follow-up question")
        app.submit()

        self.assertEqual(app._queued, ["follow-up question"])
        # The user's message is visible, but no worker was started: the
        # message history is untouched until the current turn finishes.
        self.assertEqual(len(app.messages), 1)
        self.assertIsInstance(app.blocks[-1], UserBlock)

    def test_queued_message_renders_greyed(self):
        app = make_app()
        app.set_status("thinking")
        app.input.insert("follow-up question")
        app.submit()

        block = app.blocks[-1]
        self.assertIsInstance(block, UserBlock)
        self.assertTrue(block.queued)
        lines = block.render(60)
        plain = [strip_ansi(line) for line in lines]
        # A hollow marker, not the solid one a sent message gets.
        self.assertTrue(plain[0].startswith("○ "))
        self.assertIn("queued", plain[1])

    def test_sent_message_renders_bright(self):
        app = make_app()
        app.input.insert("first message")
        app.submit()
        block = app.blocks[-1]
        self.assertFalse(block.queued)
        plain = [strip_ansi(line) for line in block.render(60)]
        self.assertTrue(plain[0].startswith("● "))
        self.assertNotIn("queued", "".join(plain))

    def test_queued_message_ungreys_when_dispatched(self):
        app = make_app()
        app.set_status("thinking")
        app.input.insert("held message")
        app.submit()
        block = app.blocks[-1]
        self.assertTrue(block.queued)

        app.set_status("idle")
        app._dispatch_next()

        self.assertFalse(block.queued)
        self.assertFalse(block.dropped)
        # It keeps its text and reads like an ordinary sent turn now.
        plain = [strip_ansi(line) for line in block.render(60)]
        self.assertTrue(plain[0].startswith("● "))

    def test_identical_queued_messages_unqueue_in_order(self):
        app = make_app()
        app.blocks.append(UserBlock(text="same", queued=True))
        app.blocks.append(UserBlock(text="same", queued=True))
        # Two identical held messages: the oldest is the one that goes out.
        app._unqueue("same")
        queued = [b for b in app.blocks
                  if isinstance(b, UserBlock) and b.queued]
        self.assertEqual(len(queued), 1)
        self.assertIs(queued[0], app.blocks[-1])

    def test_crashed_turn_marks_held_messages_unsent(self):
        app = make_app()
        app.set_status("thinking")
        app.input.insert("held message")
        app.submit()

        # A turn that dies never delivers what it was holding.
        app._drop_queue()

        block = app.blocks[-1]
        self.assertFalse(block.queued)
        self.assertTrue(block.dropped)
        self.assertEqual(app._queued, [])
        plain = [strip_ansi(line) for line in block.render(60)]
        self.assertIn("not sent", plain[1])

    def test_queued_message_dispatched_when_idle(self):
        app = make_app()
        app.set_status("thinking")
        app.input.insert("held message")
        app.submit()
        app.set_status("idle")

        app._dispatch_next()
        self.assertEqual(app._queued, [])
        # _dispatch appends the user message itself; the worker's reply
        # lands later and asynchronously, so assert on the message we sent.
        self.assertEqual(app.messages[1]["content"], "held message")

    def test_dispatch_next_goes_idle_when_queue_empty(self):
        app = make_app()
        app.set_status("thinking")
        app._dispatch_next()
        self.assertEqual(app.status, "idle")

    def test_clear_is_refused_while_busy(self):
        app = make_app()
        app.set_status("thinking")
        handled = app._run_slash_command("/clear")
        self.assertTrue(handled)
        self.assertIn("wait for the current turn", app.blocks[-1].text)
        self.assertEqual(len(app.messages), 1)  # history survived

    def test_clear_works_when_idle(self):
        app = make_app()
        app.messages.append({"role": "user", "content": "x"})
        app._run_slash_command("/clear")
        self.assertEqual(len(app.messages), 1)  # just the system prompt

    def test_slash_command_is_consumed_not_sent(self):
        app = make_app()
        app.input.insert("/help")
        app.submit()
        self.assertEqual(len(app.messages), 1)  # /help isn't a user message

    def test_ordinary_message_dispatches(self):
        app = make_app()
        app.running = False  # keep _dispatch from spawning a real turn
        app.input.insert("hello agent")
        app.submit()
        # The user message is appended synchronously; the worker's reply
        # arrives on its own thread, so look at what we sent, not the tail.
        self.assertEqual(app.messages[1]["content"], "hello agent")


class TestKeyHandling(unittest.TestCase):
    def test_ctrl_c_when_idle_quits(self):
        app = make_app()
        self.assertFalse(app.handle_key(KeyEvent(name="ctrl_c")))

    def test_ctrl_c_when_busy_requests_interrupt(self):
        app = make_app()
        app.set_status("thinking")
        self.assertTrue(app.handle_key(KeyEvent(name="ctrl_c")))
        self.assertTrue(app._interrupt.is_set())

    def test_ctrl_d_quits_on_empty_input(self):
        app = make_app()
        self.assertFalse(app.handle_key(KeyEvent(name="ctrl_d")))

    def test_ctrl_d_deletes_forward(self):
        app = make_app()
        app.input.insert("abc")
        app.input.col = 0
        self.assertTrue(app.handle_key(KeyEvent(name="ctrl_d")))
        self.assertEqual(app.input.text, "bc")

    def test_shift_enter_inserts_a_newline(self):
        app = make_app()
        app.input.insert("ab")
        app.input.col = 1
        self.assertTrue(app.handle_key(KeyEvent(name="shift_enter")))
        self.assertEqual(app.input.text, "a\nb")

    def test_alt_enter_inserts_a_newline(self):
        # Fallback for terminals where Shift+Enter isn't distinguishable.
        app = make_app()
        app.input.insert("ab")
        app.input.col = 1
        self.assertTrue(app.handle_key(KeyEvent(name="alt_enter")))
        self.assertEqual(app.input.text, "a\nb")

    def test_ctrl_j_no_longer_inserts_a_newline(self):
        # Replaced by Shift+Enter; Enter is now the only submit key.
        app = make_app()
        app.input.insert("ab")
        app.input.col = 1
        self.assertTrue(app.handle_key(KeyEvent(name="ctrl_j")))
        self.assertEqual(app.input.text, "ab")

    def test_enter_submits(self):
        app = make_app()
        app.running = False
        app.input.insert("hello")
        self.assertTrue(app.handle_key(KeyEvent(name="enter")))
        self.assertEqual(app.input.text, "")

    def test_typing_inserts(self):
        app = make_app()
        self.assertTrue(app.handle_key(KeyEvent(ch="a")))
        self.assertEqual(app.input.text, "a")

    def test_slash_opens_command_menu(self):
        app = make_app()
        app.handle_key(KeyEvent(ch="/"))
        self.assertTrue(app.menu_visible)
        self.assertTrue(any(name == "/help" for name, _ in app.menu))

    def test_typing_after_slash_filters_menu(self):
        app = make_app()
        for ch in "/cl":
            app.handle_key(KeyEvent(ch=ch))
        names = [name for name, _ in app.menu]
        self.assertIn("/clear", names)
        self.assertNotIn("/help", names)

    def test_non_slash_input_closes_menu(self):
        app = make_app()
        app.handle_key(KeyEvent(ch="/"))
        self.assertTrue(app.menu_visible)
        app.handle_key(KeyEvent(ch="x"))
        self.assertFalse(app.menu_visible)

    def test_tab_completes_selected_command(self):
        app = make_app()
        app.handle_key(KeyEvent(ch="/"))
        self.assertTrue(app.handle_key(KeyEvent(name="tab")))
        self.assertTrue(app.input.text.startswith("/"))
        self.assertFalse(app.menu_visible)

    def test_tab_inserts_spaces_outside_command_context(self):
        app = make_app()
        app.handle_key(KeyEvent(ch="a"))
        app.handle_key(KeyEvent(name="tab"))
        self.assertEqual(app.input.text, "a    ")

    def test_esc_closes_menu(self):
        app = make_app()
        app.handle_key(KeyEvent(ch="/"))
        self.assertTrue(app.handle_key(KeyEvent(name="esc")))
        self.assertFalse(app.menu_visible)

    def test_esc_interrupts_when_busy(self):
        app = make_app()
        app.set_status("thinking")
        self.assertTrue(app.handle_key(KeyEvent(name="esc")))
        self.assertTrue(app._interrupt.is_set())
        self.assertIn("Interrupted with ESC", app.blocks[-1].text)

    def test_esc_does_not_interrupt_when_idle(self):
        app = make_app()
        self.assertTrue(app.handle_key(KeyEvent(name="esc")))
        self.assertFalse(self.app_interrupt_flag(app))

    def test_esc_clears_the_draft_when_idle(self):
        app = make_app()
        app.input.insert("a half-typed message")
        self.assertTrue(app.handle_key(KeyEvent(name="esc")))
        self.assertEqual(app.input.text, "")

    @staticmethod
    def app_interrupt_flag(app) -> bool:
        return app._interrupt.is_set()

    def test_arrows_move_the_caret_in_a_multiline_draft(self):
        app = make_app()
        app.input.insert("ab")
        app.handle_key(KeyEvent(name="left"))
        self.assertEqual(app.input.col, 1)
        app.input.newline()
        app.input.insert("cd")
        # Up moves between the draft's rows.
        app.handle_key(KeyEvent(name="up"))
        self.assertEqual(app.input.row, 0)

    def test_arrows_scroll_the_transcript_in_a_single_line_draft(self):
        # Regression: Up/Down used to recall input history, which made the
        # chat unreachable from the keyboard.
        app = make_app()
        for i in range(40):
            app.blocks.append(UserBlock(text=f"message {i}"))
        app.render()
        app.input.history = ["older"]
        app.handle_key(KeyEvent(name="up"))
        self.assertEqual(app.scroll_offset, 1)
        # History was not disturbed.
        self.assertEqual(app.input.text, "")
        app.handle_key(KeyEvent(name="down"))
        self.assertEqual(app.scroll_offset, 0)

    def test_ctrl_p_and_ctrl_n_walk_history(self):
        app = make_app()
        app.input.history = ["older", "oldest"]
        app.handle_key(KeyEvent(name="ctrl_p"))
        self.assertEqual(app.input.text, "oldest")
        app.handle_key(KeyEvent(name="ctrl_p"))
        self.assertEqual(app.input.text, "older")
        app.handle_key(KeyEvent(name="ctrl_n"))
        self.assertEqual(app.input.text, "oldest")

    def test_pageup_scrolls_and_unpins(self):
        app = make_app()
        for i in range(40):
            app.blocks.append(UserBlock(text=f"message number {i}"))
        # A frame is always drawn before keys arrive, which is what measures
        # how far the transcript can actually scroll.
        app.render()
        app.handle_key(KeyEvent(name="pageup"))
        self.assertGreater(app.scroll_offset, 0)
        self.assertLessEqual(
            app.scroll_offset, app._scroll_total - app._scroll_view
        )
        self.assertFalse(app.autoscroll)
        app.handle_key(KeyEvent(name="pagedown"))
        self.assertEqual(app.scroll_offset, 0)
        self.assertTrue(app.autoscroll)

    def test_shift_arrows_scroll_one_line(self):
        app = make_app()
        for i in range(40):
            app.blocks.append(UserBlock(text=f"message number {i}"))
        app.render()
        app.handle_key(KeyEvent(name="shift_up"))
        self.assertEqual(app.scroll_offset, 1)
        self.assertFalse(app.autoscroll)
        app.handle_key(KeyEvent(name="shift_up"))
        self.assertEqual(app.scroll_offset, 2)
        app.handle_key(KeyEvent(name="shift_down"))
        self.assertEqual(app.scroll_offset, 1)
        # Landing back at the bottom re-pins the view to new output.
        app.handle_key(KeyEvent(name="shift_down"))
        self.assertEqual(app.scroll_offset, 0)
        self.assertTrue(app.autoscroll)

    def test_shift_home_end_jump_to_the_ends(self):
        app = make_app()
        for i in range(40):
            app.blocks.append(UserBlock(text=f"message number {i}"))
        app.render()
        app.handle_key(KeyEvent(name="shift_home"))
        self.assertEqual(
            app.scroll_offset, app._scroll_total - app._scroll_view
        )
        app.handle_key(KeyEvent(name="shift_end"))
        self.assertEqual(app.scroll_offset, 0)

    def test_scroll_clamps_at_the_top_and_bottom(self):
        app = make_app()
        for i in range(5):  # fits on screen: nowhere to scroll
            app.blocks.append(UserBlock(text=f"message number {i}"))
        app.render()
        app.handle_key(KeyEvent(name="pageup"))
        self.assertEqual(app.scroll_offset, 0)
        for i in range(60):
            app.blocks.append(UserBlock(text=f"more {i}"))
        app.render()
        for _ in range(20):
            app.handle_key(KeyEvent(name="pageup"))
        self.assertEqual(
            app.scroll_offset, app._scroll_total - app._scroll_view
        )

    def test_ctrl_l_resets_scroll(self):
        app = make_app()
        app.scroll_offset = 5
        app.handle_key(KeyEvent(name="ctrl_l"))
        self.assertEqual(app.scroll_offset, 0)

    def test_ctrl_u_and_ctrl_k_kill_line(self):
        app = make_app()
        app.input.insert("abcde")
        app.input.col = 3
        app.handle_key(KeyEvent(name="ctrl_u"))
        self.assertEqual(app.input.text, "de")
        app.input.col = 2
        app.handle_key(KeyEvent(name="ctrl_k"))
        self.assertEqual(app.input.text, "de")

    def test_ctrl_backspace_erases_a_word(self):
        app = make_app()
        app.input.insert("hello world")
        app.handle_key(KeyEvent(name="ctrl_backspace"))
        self.assertEqual(app.input.text, "hello ")

    def test_ctrl_w_erases_a_word(self):
        # The fallback encoding, and the one that works on every terminal.
        app = make_app()
        app.input.insert("hello world")
        app.handle_key(KeyEvent(name="ctrl_w"))
        self.assertEqual(app.input.text, "hello ")

    def test_word_erase_works_in_a_command_line(self):
        app = make_app()
        app.input.insert("/clear")
        app.handle_key(KeyEvent(name="ctrl_w"))
        self.assertEqual(app.input.text, "")
        self.assertFalse(app.menu_visible)

    def test_backspace_and_delete(self):
        app = make_app()
        app.handle_key(KeyEvent(ch="a"))
        app.handle_key(KeyEvent(name="backspace"))
        self.assertEqual(app.input.text, "")
        app.handle_key(KeyEvent(ch="a"))
        app.input.col = 0
        app.handle_key(KeyEvent(name="delete"))
        self.assertEqual(app.input.text, "")

    def test_home_end_keys(self):
        app = make_app()
        app.input.insert("abc")
        app.handle_key(KeyEvent(name="home"))
        self.assertEqual(app.input.col, 0)
        app.handle_key(KeyEvent(name="end"))
        self.assertEqual(app.input.col, 3)


class TestRendering(unittest.TestCase):
    def test_render_produces_a_full_frame(self):
        app = make_app()
        app.blocks.append(UserBlock(text="hello"))
        app.render()
        frame = app.out.getvalue()
        # A frame must position the cursor and leave it visible.
        self.assertIn("\033[", frame)

    def test_render_with_todos(self):
        from tools.todo_tools import _TODOS

        app = make_app()
        saved = _TODOS.copy()
        try:
            _TODOS[:] = [{"id": "1", "text": "a task", "status": "in_progress"}]
            app.render()
            self.assertIn("a task", app.out.getvalue())
        finally:
            _TODOS[:] = saved

    def test_render_survives_a_tiny_terminal(self):
        app = make_app()
        app.rows, app.cols = 10, 20
        app.blocks.append(UserBlock(text="x" * 500))
        # Should not raise or overflow.
        app.render()

    def test_scrolled_view_stays_put_when_new_lines_arrive(self):
        # Regression: scroll_offset is measured from the bottom, so new
        # content silently dragged a scrolled-up view back down with it.
        app = make_app()
        for i in range(40):
            app.blocks.append(UserBlock(text=f"message {i}"))
        app.render()
        app.handle_key(KeyEvent(name="pageup"))
        scrolled = app.scroll_offset
        self.assertGreater(scrolled, 0)
        for i in range(20):
            app.blocks.append(UserBlock(text=f"new message {i}"))
        app.render()
        self.assertEqual(app.scroll_offset, scrolled)

    def test_autoscroll_pins_the_view_to_newest(self):
        app = make_app()
        for i in range(40):
            app.blocks.append(UserBlock(text=f"message {i}"))
        app.render()
        app.scroll_offset = 5
        app.autoscroll = True
        app.render()
        self.assertEqual(app.scroll_offset, 0)

    def test_status_bar_shows_scroll_position_when_scrolled(self):
        app = make_app()
        for i in range(40):
            app.blocks.append(UserBlock(text=f"message {i}"))
        app.render()
        app.handle_key(KeyEvent(name="pageup"))
        self.assertIn("history", strip_ansi(app._status_bar(80)))

    def test_input_lines_never_exceed_the_width(self):
        # A line longer than the terminal must not be emitted whole -- the
        # terminal would wrap it and shift the layout down.
        app = make_app()
        app.rows, app.cols = 24, 40
        app.input.insert("x" * 500)
        lines = app._input_box_lines(40, app._input_height())[1:]  # skip rule
        for line in lines:
            self.assertLessEqual(
                len(strip_ansi(line)), 40, line[:40]
            )

    def test_input_scrolls_to_keep_the_caret_visible(self):
        app = make_app()
        app.rows, app.cols = 24, 40
        app.input.insert("x" * 500)
        col = app._caret_col()
        self.assertLessEqual(col, 40)
        self.assertGreaterEqual(col, len("> "))

    def test_input_caret_column_tracks_scrolling(self):
        app = make_app()
        app.rows, app.cols = 24, 40
        app.input.insert("x" * 500)
        app.input.col = 0
        self.assertEqual(app._caret_col(), len("> "))
        # Moving the caret right should keep it on screen, not run past 40.
        app.input.col = 300
        self.assertLessEqual(app._caret_col(), 40)

    def test_short_input_is_not_scrolled(self):
        app = make_app()
        app.rows, app.cols = 24, 80
        app.input.insert("short")
        self.assertEqual(app._input_scroll(78), 0)
        self.assertEqual(app._caret_col(), len("> ") + 5)

    def test_status_bar_never_overflows_a_narrow_terminal(self):
        # A bar wider than the screen wraps and shoves the input box down.
        app = make_app()
        app.set_status("thinking")
        app._queued.append("held")
        for width in (20, 40, 60):
            bar = app._status_bar(width)
            self.assertLessEqual(
                text_width(strip_ansi(bar)), width, f"bar at width {width}"
            )

    def test_status_bar_shows_pending_approval(self):
        app = make_app()
        with app._approval_lock:
            app._pending_approval = {
                "description": "run_shell: echo hi",
                "event": threading.Event(),
                "result": False,
            }
        bar = app._status_bar(80)
        self.assertIn("approve?", strip_ansi(bar))

    def test_status_bar_shows_queued_count(self):
        app = make_app()
        app.set_status("thinking")
        app._queued.append("held")
        bar = app._status_bar(80)
        self.assertIn("1 queued", strip_ansi(bar))

    def test_status_bar_shows_yolo_when_on(self):
        app = make_app(auto_approve=True)
        app.set_status("idle")
        bar = app._status_bar(80)
        self.assertIn("yolo", strip_ansi(bar))

    def test_header_shows_usage(self):
        app = make_app()
        app.last_usage = {"total_tokens": 1500, "prompt_tokens": 600,
                          "completion_tokens": 900}
        header = strip_ansi(app._header(120))
        self.assertIn("1.5k tok", header)
        self.assertIn("600↑", header)

    def test_streamed_text_marks_the_count_as_an_estimate(self):
        app = make_app()
        app.last_usage = {"total_tokens": 1500, "prompt_tokens": 600,
                          "completion_tokens": 900}
        cb = TuiCallbacks(app)
        cb.on_content_delta("hello world" * 100)  # 1100 chars ~= 275 tokens
        header = strip_ansi(app._header(120))
        # The estimate is added on and flagged, so nobody reads it as billed.
        self.assertIn("~1.8k tok", header)
        self.assertEqual(app._in_flight_chars, 1100)

    def test_streamed_count_climbs_as_text_arrives(self):
        app = make_app()
        app.last_usage = {"total_tokens": 1000}
        cb = TuiCallbacks(app)
        first = strip_ansi(app._header(120))
        cb.on_content_delta("a" * 400)
        second = strip_ansi(app._header(120))
        # The number moves between frames; the "~" is what says it's live.
        self.assertNotEqual(first, second)
        self.assertNotIn("~", first)
        self.assertIn("~", second)

    def test_reported_usage_replaces_the_estimate(self):
        app = make_app()
        app.last_usage = {"total_tokens": 1000}
        cb = TuiCallbacks(app)
        cb.on_content_delta("a" * 4000)  # ~1000 tokens of streaming
        self.assertGreater(app._in_flight_chars, 0)
        cb.on_usage({"total_tokens": 950, "prompt_tokens": 200,
                     "completion_tokens": 750})
        # The real figure supersedes the estimate and clears the counter.
        self.assertEqual(app._in_flight_chars, 0)
        header = strip_ansi(app._header(120))
        self.assertIn("1.9k tok", header)  # 1000 + 950
        self.assertNotIn("~", header)

    def test_idle_clears_a_stale_estimate(self):
        # A provider that never sends usage must not leave a climbing "~".
        app = make_app()
        app.last_usage = {"total_tokens": 500}
        cb = TuiCallbacks(app)
        cb.on_content_delta("a" * 4000)
        cb.on_status("idle")
        self.assertEqual(app._in_flight_chars, 0)
        header = strip_ansi(app._header(120))
        self.assertNotIn("~", header)


class TestTokenFormatting(unittest.TestCase):
    def test_small_counts_are_plain(self):
        self.assertEqual(_fmt_tokens(0), "0")
        self.assertEqual(_fmt_tokens(999), "999")

    def test_thousands_get_a_k_suffix(self):
        self.assertEqual(_fmt_tokens(1500), "1.5k")

    def test_millions_get_an_m_suffix(self):
        self.assertEqual(_fmt_tokens(2_500_000), "2.5M")


class TestSlashCommands(unittest.TestCase):
    def test_unknown_command_warns(self):
        app = make_app()
        self.assertTrue(app._run_slash_command("/nonsense"))
        self.assertIn("unknown command", app.blocks[-1].text)

    def test_help_lists_every_command(self):
        app = make_app()
        app._run_slash_command("/help")
        text = app.blocks[-1].text
        for name in ("/clear", "/yolo", "/model", "/todos", "/plugins"):
            self.assertIn(name, text)

    def test_yolo_toggles(self):
        app = make_app()
        self.assertFalse(app.auto_approve)
        app._run_slash_command("/yolo")
        self.assertTrue(app.auto_approve)
        app._run_slash_command("/yolo")
        self.assertFalse(app.auto_approve)

    def test_todos_toggles(self):
        app = make_app()
        self.assertTrue(app.show_todos)
        app._run_slash_command("/todos")
        self.assertFalse(app.show_todos)

    def test_save_and_load_round_trip(self):
        app = make_app()
        app.messages.append({"role": "user", "content": "saved message"})
        app._run_slash_command("/save unittest-session")
        try:
            self.assertTrue(
                any(isinstance(b, SystemBlock) and "saved" in b.text
                    for b in app.blocks)
            )

            other = make_app()
            other._run_slash_command("/load unittest-session")
            self.assertTrue(
                any(m.get("content") == "saved message" for m in other.messages)
            )
        finally:
            from cli import SESSIONS_DIR

            session = SESSIONS_DIR / "unittest-session.json"
            if session.exists():
                session.unlink()

    def test_save_without_name_is_usage_error(self):
        app = make_app()
        app._run_slash_command("/save")
        self.assertIn("usage", app.blocks[-1].text)

    def test_load_missing_session_reports_error(self):
        app = make_app()
        app._run_slash_command("/load definitely-not-a-real-session")
        self.assertIn("no saved session", app.blocks[-1].text)

    def test_exit_sets_running_false(self):
        app = make_app()
        app._run_slash_command("/exit")
        self.assertFalse(app.running)

    def test_plugins_command(self):
        app = make_app()
        app._run_slash_command("/plugins")
        self.assertTrue(app.blocks[-1].text.startswith("plugins:"))


if __name__ == "__main__":
    unittest.main()
