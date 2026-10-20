"""Native peers accept their own Apple signing team, without runtime overrides."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_ID = 'ai.callabo.tacto'
HELPER_ID = 'ai.callabo.tacto.helper'


def requirement(identifier, team):
    return f'identifier "{identifier}" and anchor apple generic and certificate leaf[subject.OU] = "{team}"'


def shell_requirement(identifier, team):
    return subprocess.run(['bash','-c','. "$1"; tacto_requirement "$2" "$3"',
                           'signing-test',str(ROOT/'signing.sh'),identifier,team],
                          capture_output=True,text=True)


class ShellDefinition(unittest.TestCase):
    def test_identifier_transition_warns_even_when_team_is_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            old = Path(folder) / 'previous'
            replacement = Path(folder) / 'replacement'
            old.touch(); replacement.touch()
            result = subprocess.run(['bash', '-c', '''
. "$1"
codesign() {
  case "${@: -1}" in
    */previous) printf 'Identifier=ai.example.previous\nTeamIdentifier=ABCDE12345\n' >&2 ;;
    *) printf 'Identifier=ai.callabo.tacto\nTeamIdentifier=ABCDE12345\n' >&2 ;;
  esac
}
tacto_warn_team_change "$2" "$3"
''', 'transition-test', str(ROOT/'signing.sh'), str(old), str(replacement)], capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn('Signing identifier changed: ai.example.previous -> ai.callabo.tacto', result.stderr)
            self.assertIn('Accessibility and Screen Recording', result.stderr)
            self.assertNotIn('Signing team changed', result.stderr)

    def test_requirements_work_for_distinct_teams_and_identifiers(self):
        for team in ['ABCDE12345','ZYXWV98765']:
            for identifier in [LAUNCHER_ID,HELPER_ID]:
                result=shell_requirement(identifier,team)
                self.assertEqual(0,result.returncode,result.stderr)
                self.assertEqual(requirement(identifier,team),result.stdout)

    def test_invalid_teams_and_requirement_injection_are_refused(self):
        for identifier,team in [(HELPER_ID,''),(HELPER_ID,'unsigned'),(HELPER_ID,'abcde12345'),
                                (HELPER_ID,'ABCDE12345" or true'),('id" or true','ABCDE12345')]:
            result=shell_requirement(identifier,team)
            self.assertNotEqual(0,result.returncode)
            self.assertEqual('',result.stdout)

    def test_every_signing_script_uses_verified_shared_signing(self):
        for name in ['launcher/build.sh','helper/build.sh','helper/bundle.sh']:
            code=(ROOT/name).read_text()
            self.assertIn('. ../signing.sh',code)
            self.assertIn('tacto_select_identity',code)
            self.assertIn('tacto_sign ',code)
        code=(ROOT/'signing.sh').read_text()
        self.assertIn('codesign --verify --strict',code)
        self.assertNotIn('subject.CN',code)
        self.assertNotIn('48H4RZNB97',code)


class SwiftAcceptancePredicates(unittest.TestCase):
    def test_both_peers_use_shared_signature_derived_policy(self):
        for name,variable,identifier in [('helper/TactoHelper.swift','clientRequirement',LAUNCHER_ID),
                                         ('launcher/tacto.swift','helperRequirement',HELPER_ID)]:
            code=(ROOT/name).read_text()
            self.assertIn(f'let {variable} = TactoSigning.peerRequirement(identifier: "{identifier}")',code)
        for name in ['launcher/build.sh','helper/build.sh']:
            self.assertIn('../SigningPolicy.swift',(ROOT/name).read_text())

    def test_policy_checks_apple_anchor_before_accepting_self_team(self):
        code=(ROOT/'SigningPolicy.swift').read_text()
        self.assertLess(code.index('SecCodeCheckValidity'),code.index('kSecCodeInfoTeamIdentifier'))
        self.assertIn('SecCodeCopySelf',code)
        self.assertIn('exit(78)',code)
        self.assertNotIn('ProcessInfo.processInfo.environment',code)
        self.assertNotIn('getenv(',code)


class SignedArtifacts(unittest.TestCase):
    def test_built_peers_keep_identifier_and_actual_team_requirements(self):
        teams=[]
        for name,identifier in [('launcher/tacto',LAUNCHER_ID),('helper/tacto-helper',HELPER_ID),
                                ('helper/Tacto Helper.app',HELPER_ID)]:
            target=ROOT/name
            if not target.exists():self.skipTest('native artifacts not built')
            meta=subprocess.run(['codesign','-dv','--verbose=4',str(target)],capture_output=True,text=True)
            team=re.search(r'^TeamIdentifier=([A-Z0-9]{10})$',meta.stderr,re.M)
            if not team:self.skipTest('artifact lacks Apple team signature')
            teams.append(team[1]);req=requirement(identifier,team[1])
            checked=subprocess.run(['codesign','--verify','--strict','-R='+req,str(target)],capture_output=True,text=True)
            self.assertEqual(0,checked.returncode,checked.stderr)
            designated=subprocess.run(['codesign','-d','-r-',str(target)],capture_output=True,text=True)
            self.assertIn('designated => '+req,designated.stdout)
        self.assertEqual(1,len(set(teams)))


@unittest.skipUnless(os.environ.get('TACTO_TEST_SIGNED_POLICY')=='1','opt-in: sign disposable policy probe')
class LiveSigningPolicy(unittest.TestCase):
    def test_unsigned_correct_team_wrong_team_identifier_and_environment(self):
        with tempfile.TemporaryDirectory(prefix='tacto-signing-policy-') as temp:
            work=Path(temp);main=work/'main.swift';binary=work/'probe'
            main.write_text('''import Foundation
import Security
let a=CommandLine.arguments
if a[1]=="own" { print(TactoSigning.ownTeam() ?? "UNTRUSTED") }
else if a[1]=="peer" { print(TactoSigning.peerRequirement(identifier:"ai.callabo.tacto.helper")) }
else if let text=TactoSigning.requirement(identifier:a[3],team:a[4]) {
 var code:SecStaticCode?;var req:SecRequirement?
 let loaded=SecStaticCodeCreateWithPath(URL(fileURLWithPath:a[2]) as CFURL,[],&code)
 let parsed=SecRequirementCreateWithString(text as CFString,[],&req)
 print(loaded==errSecSuccess && parsed==errSecSuccess && code != nil && req != nil && SecStaticCodeCheckValidity(code!,[],req!)==errSecSuccess)
} else { print(false) }
''')
            built=subprocess.run(['xcrun','swiftc',str(main),str(ROOT/'SigningPolicy.swift'),'-o',str(binary)],capture_output=True,text=True,timeout=60)
            self.assertEqual(0,built.returncode,built.stderr)
            def run(*args,**kwargs):return subprocess.run([str(binary),*map(str,args)],capture_output=True,text=True,timeout=15,**kwargs)
            self.assertEqual('UNTRUSTED',run('own').stdout.strip())
            self.assertEqual(78,run('peer').returncode)
            signed=subprocess.run(['bash','-c','. "$1"; tacto_select_identity && tacto_sign "$2" ai.callabo.tacto.policy-test',
                                   'policy-test',str(ROOT/'signing.sh'),str(binary)],capture_output=True,text=True,timeout=30)
            self.assertEqual(0,signed.returncode,signed.stderr)
            team=run('own').stdout.strip();self.assertRegex(team,r'^[A-Z0-9]{10}$')
            self.assertEqual('true',run('check',binary,'ai.callabo.tacto.policy-test',team).stdout.strip())
            other='ABCDE12345' if team!='ABCDE12345' else 'ZYXWV98765'
            self.assertEqual('false',run('check',binary,'ai.callabo.tacto.policy-test',other).stdout.strip())
            self.assertEqual('false',run('check',binary,'ai.callabo.tacto.wrong',team).stdout.strip())
            env={**os.environ,'TACTO_TEAM_ID':other,'TACTO_HELPER_REQUIREMENT':'true'}
            self.assertEqual(requirement(HELPER_ID,team),run('peer',env=env).stdout.strip())
