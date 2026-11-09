#!/usr/bin/env python3
"""Source-level guardrails; these do not substitute for Android runtime tests."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2] / 'packages/ArtisanControl/src/com/artisanrom/control'


class RuntimeSourceGuards(unittest.TestCase):
    def source(self, name):
        return (ROOT / name).read_text()

    def test_volume_release_cancels_pending_press_before_mode_gate(self):
        text = self.source('SystemHooks.java')
        start = text.index('public static synchronized boolean music(')
        end = text.index('public static boolean powerMenu(', start)
        music = text[start:end]
        self.assertLess(music.index('event.getAction() == KeyEvent.ACTION_UP'),
                        music.index('audio.getMode() != AudioManager.MODE_NORMAL'))
        self.assertIn('existing.cancelled=true', music)
        self.assertIn('handler.removeCallbacks(existing)', music)
        self.assertIn('cancelled || audio.getMode() != AudioManager.MODE_NORMAL', text)
        self.assertIn('AudioManager.OnModeChangedListener', text)
        self.assertIn('audio.addOnModeChangedListener(Runnable::run, this)', text)
        self.assertIn('audio.removeOnModeChangedListener(this)', text)

    def test_launcher_previous_data_removed_after_transaction(self):
        text = self.source('RootBroker.java')
        start = text.index('Path previous;')
        end = text.index('private static void removeTree', start)
        transaction = text[start:end]
        self.assertLess(transaction.index('Files.move(previous.resolve(scope)'),
                        transaction.rindex('removeTree(previous)'))
        self.assertLess(transaction.index('finally { try{removeTree(stage);}finally{homeStart();} }'),
                        transaction.rindex('removeTree(previous)'))

    def test_status_loop_retries_and_reattachment_is_deduplicated(self):
        text = self.source('UiHooks.java')
        start = text.index('public static void statusBar(')
        end = text.index('private static void updateBar', start)
        status = text[start:end]
        self.assertIn('bars.containsKey(root)', status)
        self.assertIn('finally { if(root.isAttachedToWindow()) root.postDelayed(this, delay); }', status)
        self.assertIn('onViewAttachedToWindow(View view) { root.removeCallbacks(updater); root.post(updater); }', status)
        self.assertIn('onViewDetachedFromWindow(View view) { root.removeCallbacks(updater); }', status)


if __name__ == '__main__':
    unittest.main()
