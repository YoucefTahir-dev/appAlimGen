"""Synthetic process doubles only: never launch an ERP or read its settings."""
from pathlib import Path
import signal
import subprocess
import unittest
from unittest.mock import Mock, patch

from scripts import run_colocated_reset as runtime


class ColocatedResetSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.env = {'PASSWORD_RESET_COLOCATED_WORKER_ENABLED': 'true'}
        self.runtime = runtime.Supervisor(env=self.env)
        self.worker = Mock(pid=101)
        self.web = Mock(pid=102)
        self.worker.poll.return_value = self.web.poll.return_value = None
        self.enterContext(patch.object(runtime.signal, 'signal'))
        self.enterContext(patch.object(runtime.signal, 'SIGKILL', 9, create=True))
        self.popen = self.enterContext(patch.object(runtime.subprocess, 'Popen',
                                                   side_effect=[self.worker, self.web]))
        self.kill = self.enterContext(patch.object(runtime.os, 'killpg', create=True))
        self.marker = self.enterContext(patch.object(Path, 'is_file', return_value=True))
        self.progress = self.enterContext(patch.object(runtime, 'progress_token', return_value=1))
        self.sleep = self.enterContext(patch.object(runtime.time, 'sleep',
                                                   side_effect=lambda _: self.runtime.request_stop()))

    def test_off_is_zero_processes(self):
        self.runtime.env = {}
        self.assertEqual(self.runtime.run(), 2)
        self.popen.assert_not_called()

    def test_worker_is_ready_before_web_and_commands_keep_logging_private(self):
        self.assertEqual(self.runtime.run(), 0)
        calls = self.popen.call_args_list
        self.assertIn('--ready-file', calls[0].args[0])
        self.assertIn('process_password_resets', calls[0].args[0])
        self.assertIn('gunicorn', calls[1].args[0])
        self.assertIn('%(m)s %(U)s %(s)s', calls[1].args[0])
        self.assertNotIn('%(q)s', str(calls))
        for call in calls:
            self.assertTrue(call.kwargs['start_new_session'])
            self.assertEqual(call.kwargs['stdin'], subprocess.DEVNULL)
            self.assertNotIn('shell', call.kwargs)
        self.assertEqual(self.kill.call_args_list[0].args, (101, signal.SIGTERM))
        self.worker.wait.assert_called()
        self.web.wait.assert_called()

    def test_dead_worker_never_starts_web(self):
        self.worker.poll.return_value = 0
        self.assertEqual(self.runtime.run(), 1)
        self.assertEqual(self.popen.call_count, 1)

    def test_startup_timeout_never_starts_web(self):
        self.marker.return_value = False
        self.runtime.startup_seconds = 0
        self.assertEqual(self.runtime.run(), 1)
        self.assertEqual(self.popen.call_count, 1)

    def test_stop_during_startup_never_starts_web(self):
        self.marker.return_value = False
        self.assertEqual(self.runtime.run(), 0)
        self.assertEqual(self.popen.call_count, 1)

    def test_unexpected_web_exit_fails_whole_unit(self):
        self.web.poll.return_value = 0
        self.assertEqual(self.runtime.run(), 1)
        self.assertEqual(self.popen.call_count, 2)

    def test_unexpected_worker_exit_fails_whole_unit(self):
        self.worker.poll.side_effect = [None, 1]
        self.assertEqual(self.runtime.run(), 1)
        self.web.wait.assert_called()

    def test_live_but_stuck_worker_fails_after_five_minutes_without_progress(self):
        with patch.object(runtime.time, 'monotonic', side_effect=[0, 0, 301, 301, 301, 301]):
            self.assertEqual(self.runtime.run(), 1)
        self.sleep.assert_not_called()
        self.web.wait.assert_called()

    def test_new_heartbeat_resets_progress_deadline(self):
        self.progress.side_effect = [1, 2]
        with patch.object(runtime.time, 'monotonic', return_value=400):
            self.assertEqual(self.runtime.run(), 0)

    def test_web_spawn_failure_still_stops_worker(self):
        self.popen.side_effect = [self.worker, OSError('synthetic-private-detail')]
        with self.assertRaises(OSError):
            self.runtime.run()
        self.worker.wait.assert_called()
        self.kill.assert_any_call(101, signal.SIGTERM)

    def test_heartbeat_directory_outlives_children(self):
        original_stop = self.runtime.stop_children

        def stop_with_directory_check():
            marker = Path(self.popen.call_args_list[0].args[0][-1])
            self.assertTrue(marker.parent.is_dir())
            original_stop()

        with patch.object(self.runtime, 'stop_children', side_effect=stop_with_directory_check):
            self.assertEqual(self.runtime.run(), 0)

    def test_shutdown_escalation_and_already_exited_group_are_handled(self):
        self.worker.wait.side_effect = [subprocess.TimeoutExpired('worker', 30), 0]
        self.kill.side_effect = [ProcessLookupError, None, ProcessLookupError, None]
        self.assertEqual(self.runtime.run(), 0)
        self.assertEqual(self.kill.call_count, 4)

    def test_invalid_web_limits_start_nothing(self):
        for variable, value in (('PORT', '0'), ('WEB_CONCURRENCY', '0'), ('WEB_TIMEOUT', '601'),
                                ('PORT', '8000;echo private')):
            with self.subTest(variable=variable):
                self.runtime.env = self.env | {variable: value}
                with self.assertRaises(ValueError):
                    self.runtime.run()
        self.popen.assert_not_called()

    def test_default_web_command_matches_existing_resource_settings(self):
        command = runtime.web_command({})
        self.assertEqual(command[command.index('--workers') + 1], '2')
        self.assertEqual(command[command.index('--timeout') + 1], '120')
        self.assertEqual(command[command.index('--bind') + 1], '0.0.0.0:8000')

    def test_native_entrypoint_is_linux_only(self):
        with patch.object(runtime.os, 'name', 'nt'), patch('builtins.print'):
            self.assertEqual(runtime.main(), 2)
        self.popen.assert_not_called()

    def test_main_suppresses_exception_message(self):
        with patch.object(runtime.os, 'name', 'posix'), \
                patch.object(runtime.Supervisor, 'run', side_effect=OSError('synthetic-private-detail')), \
                patch('builtins.print') as output:
            self.assertEqual(runtime.main(), 1)
        self.assertNotIn('synthetic-private-detail', str(output.call_args_list))


if __name__ == '__main__':
    unittest.main()
