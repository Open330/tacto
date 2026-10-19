import unittest
from unittest.mock import patch
from tacto import onboard

class OnboardingSnapshotTests(unittest.TestCase):
    def test_denied_helper_is_checked_once_without_shared_python_advice(self):
        state={'built':True,'trusted':False,'bundled':True,'path':'/owned/Tacto Helper.app',
               'name':'Tacto Helper','why':'접근성 권한이 필요합니다'}
        lines=[]
        check=onboard.Check('accessibility',False,'accessibility','enable','x-test')
        with patch.object(onboard,'helper_status',side_effect=[state,OSError('Bad file descriptor')]) as status, \
             patch.object(onboard,'check_all',return_value=[check]):
            self.assertFalse(onboard.run(interactive=False,narrate=lines.append))
        status.assert_called_once()
        self.assertNotIn('Bad file descriptor','\n'.join(lines))
        self.assertNotIn('공유 파이썬','\n'.join(lines))
        self.assertIn('접근성 권한이 필요합니다','\n'.join(lines))
