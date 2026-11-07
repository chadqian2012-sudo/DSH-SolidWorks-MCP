"""Real sync script with isolated source/profile/state; no installed DSH paths."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
REPO=Path(__file__).resolve().parents[3]
class SyncTests(unittest.TestCase):
    def test_runtime_exclusion_dryrun_backups_hash_and_rollback(self):
        with tempfile.TemporaryDirectory(prefix='s07-sync-') as tmp:
            root=Path(tmp);source=root/'source';source.mkdir(); dsh=root/'dsh';state=dsh/'.agent-presets'/'engineering'/'tools';state.mkdir(parents=True)
            profile=dsh/'profiles'/'webots-test';profile.mkdir(parents=True);(profile/'package.json').write_text('{"dependencies":{"dsh-engineering-ui":"file:./node_modules/dsh-engineering-ui"}}')
            shutil.copy2(REPO/'sync-to-dsh.ps1',source/'sync-to-dsh.ps1')
            tools=source/'engineering'/'tools';tools.mkdir(parents=True)
            shutil.copy2(REPO/'engineering'/'tools'/'profile-manifest.ps1', tools)
            (tools/'code.py').write_text('new code')
            (state/'code.py').write_text('old code')
            runtime=['webots_state.json','webots_jobs.sqlite3','webots_jobs.sqlite3-wal','webots_jobs.sqlite3-shm','webots_instance.lock','mode_state.json']
            for name in runtime: (tools/name).write_text('source runtime');(state/name).write_text('live runtime')
            for parent in ('webots_runs','nested/webots_runs'):
                folder=tools/parent;folder.mkdir(parents=True);(folder/'keep.txt').write_text('must not sync')
            own=state/'webots_runs'/'own';own.mkdir(parents=True);(own/'result.json').write_text('live report')
            env={**os.environ,'DSH_HOME':str(dsh),'DSH_STATE_DIR':str(state),'DSH_ENGINEERING_ROOT':str(source/'engineering')}
            # PowerShell 7's module path cannot be reused by Windows PowerShell 5.1.
            env = {key:value for key,value in env.items() if key.upper() != 'PSMODULEPATH'}
            def run(*args):
                result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(source/'sync-to-dsh.ps1'),'-Profile','webots-test',*args],env=env,capture_output=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
            run('-DryRun');self.assertEqual((state/'code.py').read_text(),'old code');self.assertFalse(list(state.rglob('*.bak-sync-*')))
            run();self.assertEqual((state/'code.py').read_bytes(),(tools/'code.py').read_bytes())
            for name in runtime: self.assertEqual((state/name).read_text(),'live runtime',name)
            self.assertFalse((state/'nested'/'webots_runs').exists());self.assertFalse((state/'webots_runs'/'keep.txt').exists());self.assertEqual((own/'result.json').read_text(),'live report')
            backups=list(state.glob('code.py.bak-sync-*'));self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_text(),'old code')
            run();self.assertEqual(len(list(state.glob('code.py.bak-sync-*'))),1)
            shutil.copy2(backups[0],state/'code.py');self.assertEqual((state/'code.py').read_text(),'old code')
            # A code-only sync still refreshes an installed copy without editing a damaged manifest.
            plugin = source/'engineering/plugins/dsh-engineering-ui'
            plugin.mkdir(parents=True)
            (plugin/'index.js').write_text('new')
            installed = profile/'node_modules/dsh-engineering-ui'
            installed.mkdir(parents=True)
            (profile/'package.json').write_text('{broken')
            run()
            self.assertEqual((installed/'index.js').read_text(), 'new')
            self.assertEqual((profile/'package.json').read_text(), '{broken')

    def test_legacy_installer_preserves_runtime_and_standard_permissions(self):
        with tempfile.TemporaryDirectory(prefix='s07-legacy-install-') as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();dsh=root/'dsh'
            for name in ('install-0.2.0.ps1','sync-to-dsh.ps1'):
                shutil.copy2(REPO/name,source/name)
            tools=source/'engineering/tools';tools.mkdir(parents=True)
            shutil.copy2(REPO/'engineering/tools/profile-manifest.ps1',tools)
            (tools/'code.py').write_text('new code')
            shutil.copy2(REPO/'engineering/install-plugin.ps1',source/'engineering/install-plugin.ps1')
            state=dsh/'.agent-presets/engineering/tools';state.mkdir(parents=True)
            runtime=('settings.json','custom_materials.json','mode_state.json','sw_monitor.pid')
            for name in runtime:
                (tools/name).write_text('source state');(state/name).write_text('user state')
            plugin=source/'engineering/plugins/dsh-engineering-ui';(plugin/'lib').mkdir(parents=True)
            for name in ('package.json','preset-engineering.patch.yml','lib/index.js'):
                (plugin/name).write_text('{}')
            legacy='      sw-single-line:\n        sandbox: workspace-write\n\n        approval: never\n'
            patch='# sw-single-line is retired; preserve this comment\n- id: persona\n  config:\n    prefix: |\n      sw-single-line:\n        keep this user text\n    presets:\n      sw-single-line:\n        custom: keep\n    nested:\n      - id: permission\n        config:\n          presets:\n            sw-single-line:\n              custom: keep\n- id: permission\n  config:\n    defaultPreset: workspace-write\n    presets:\n      read-only:\n        approval: ask\n'+legacy+'      workspace-write:\n        approval: ask\n      danger-full-access:\n        approval: never\n'
            for name in ('desktop','web','untargeted'):
                profile=dsh/'profiles'/name;profile.mkdir(parents=True)
                manifest={'dependencies':{'existing':'1.0.0'},'custom':'keep','dsh':{'custom':'keep','profile':{'custom':'keep','bundles':['@deepseek-ai/dsh-base','@deepseek-ai/dsh-web-app']}}}
                (profile/'package.json').write_text(json.dumps(manifest))
                (profile/'cordis.patch.yml').write_bytes(patch.replace('\n','\r\n' if name=='web' else '\n').encode())
            env={key:value for key,value in os.environ.items() if key.upper()!='PSMODULEPATH'}
            env['DSH_HOME']=str(dsh)
            wrapper=source/'selected.ps1'
            wrapper.write_text("& (Join-Path $PSScriptRoot 'install-0.2.0.ps1') -Profile @('desktop', 'web')")
            for script in (wrapper,source/'install-0.2.0.ps1'):
                result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script)],env=env,capture_output=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
                for name in runtime:self.assertEqual((state/name).read_text(),'user state',name)
                for name in ('desktop','web','untargeted'):
                    profile=dsh/'profiles'/name;manifest=json.loads((profile/'package.json').read_text())
                    targeted=script!=wrapper or name!='untargeted'
                    self.assertEqual('dsh-engineering-ui' in manifest['dependencies'],targeted)
                    self.assertEqual(manifest['custom'],'keep');self.assertEqual(manifest['dsh']['custom'],'keep');self.assertEqual(manifest['dsh']['profile']['custom'],'keep')
                    expected=patch.replace(legacy,'\n') if targeted else patch
                    self.assertEqual((profile/'cordis.patch.yml').read_text(),expected)
                    self.assertEqual(len(list(profile.glob('cordis.patch.yml.bak-*'))),int(targeted))
            self.assertEqual((state/'code.py').read_text(),'new code')
            helper=source/'engineering/install-plugin.ps1'
            result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(helper)],env=env,capture_output=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
            self.assertEqual(len(list((dsh/'profiles/web').glob('cordis.patch.yml.bak-*'))),1)
            # Registration errors must reach the legacy entry point's exit code.
            for invalid in ('{bad json','null','[]','[{}]'):
                (dsh/'profiles/desktop/package.json').write_text(invalid)
                result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(source/'install-0.2.0.ps1')],env=env,capture_output=True,timeout=30)
                self.assertNotEqual(result.returncode,0,invalid)
            outside=dsh/'outside';outside.mkdir();(outside/'package.json').write_text('{}')
            for script in ('install-0.2.0.ps1','sync-to-dsh.ps1'):
                result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(source/script),'-Profile','../outside'],env=env,capture_output=True,timeout=30)
                self.assertNotEqual(result.returncode,0,script)
                self.assertFalse((outside/'node_modules').exists())

    def test_bad_profile_stops_all_changes_before_install_or_uninstall(self):
        with tempfile.TemporaryDirectory(prefix='s07-profile-preflight-') as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();home=root/'home'
            for name in ('sync-to-dsh.ps1','uninstall-0.2.0.ps1'):
                shutil.copy2(REPO/name,source/name)
            tools=source/'engineering/tools';tools.mkdir(parents=True)
            shutil.copy2(REPO/'engineering/tools/profile-manifest.ps1',tools)
            (tools/'code.py').write_text('new code')
            plugin=source/'engineering/plugins/dsh-engineering-ui';plugin.mkdir(parents=True)
            (plugin/'package.json').write_text('{"name":"dsh-engineering-ui"}')
            state=home/'.agent-presets/engineering/tools';state.mkdir(parents=True)
            (state/'code.py').write_text('installed code')
            for name,text in (('desktop','{}'),('web','{damaged JSON')):
                profile=home/'profiles'/name;installed=profile/'node_modules/dsh-engineering-ui';installed.mkdir(parents=True)
                (profile/'package.json').write_text(text);(installed/'package.json').write_text('installed plugin')
            env={key:value for key,value in os.environ.items() if key.upper()!='PSMODULEPATH'};env['DSH_HOME']=str(home)
            def run(script,*args):
                return subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(source/script),*args],env=env,capture_output=True,timeout=30)
            before={str(p.relative_to(home)):p.read_bytes() for p in home.rglob('*') if p.is_file()}
            for script,args in (('sync-to-dsh.ps1',('-Register',)),('uninstall-0.2.0.ps1',('-Shared',))):
                result=run(script,*args)
                self.assertNotEqual(result.returncode,0,script)
                self.assertEqual({str(p.relative_to(home)):p.read_bytes() for p in home.rglob('*') if p.is_file()},before,script)
            (home/'profiles/web/package.json').write_text('{}')
            for name in ('webots-simulation','user-custom'):
                skill=home/'skills'/name;skill.mkdir(parents=True);(skill/'SKILL.md').write_text('keep custom skill')
            result=run('uninstall-0.2.0.ps1','-Shared')
            self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
            self.assertFalse((home/'skills/webots-simulation').exists())
            self.assertEqual((home/'skills/user-custom/SKILL.md').read_text(),'keep custom skill')
if __name__=='__main__':unittest.main()
