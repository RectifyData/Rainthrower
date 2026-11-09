#!/usr/bin/env python3
"""Exercise the real Java/shell lock protocol across processes on host fixtures."""
from pathlib import Path
import subprocess
import tempfile
from test_s10_boot_performance import Fixture

ROOT = Path(__file__).resolve().parents[2]
PROBE = '''package com.artisanrom.control;
import java.nio.file.Paths;
public class LockProbe {
 public static void main(String[] args)throws Exception {
  try(RootBrokerPerformanceLock lock=RootBrokerPerformanceLock.acquire(Paths.get(args[0]))) {
   System.out.println("LOCKED");System.out.flush();System.in.read();
  }
 }
}'''


def main():
    with tempfile.TemporaryDirectory(prefix='s10-node-lock-') as directory:
        root = Path(directory)
        probe = root / 'LockProbe.java'
        probe.write_text(PROBE)
        output = root / 'classes'
        output.mkdir()
        package = ROOT / 'packages/ArtisanControl/src/com/artisanrom/control'
        stubs = {
            'android/os/Build.java': 'package android.os; public class Build { public static String MODEL="test"; }',
            'android/os/SystemClock.java': 'package android.os; public class SystemClock { public static long elapsedRealtime(){return 0;} }',
            'android/system/Os.java': 'package android.system; public class Os { public static void chmod(String p,int m){} }',
            'org/json/JSONArray.java': '''package org.json; public class JSONArray {
                public JSONArray(){} public int length(){return 0;} public double getDouble(int i){return 0;}
                public JSONArray put(double x){return this;} }''',
            'org/json/JSONObject.java': '''package org.json; public class JSONObject {
                public JSONObject(){} public JSONObject(String s){} public int optInt(String s){return 0;}
                public String optString(String s){return "";} public String getString(String s){return "";}
                public int getInt(String s){return 0;} public double getDouble(String s){return 0;}
                public JSONArray getJSONArray(String s){return new JSONArray();} public int length(){return 0;}
                public Object get(String s){return null;} public JSONObject put(String s,Object o){return this;}
                public String toString(int i){return "{}";} }''',
        }
        files = []
        for name, contents in stubs.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents)
            files.append(str(path))
        sources = [package / ('RootBrokerPerformance' + suffix + '.java') for suffix in ('', 'Lock', 'Policy', 'Learner')]
        subprocess.run(['javac', '--release', '8', '-d', str(output), *map(str, sources), str(probe), *files], check=True)
        fixture_root = root / 'fixture'
        fixture_root.mkdir()
        fixture = Fixture(fixture_root)
        before = fixture.values()
        lock = fixture.state / 'nodes.lock'
        command = ['java', '-cp', str(output), 'com.artisanrom.control.LockProbe', str(lock)]
        holder = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            assert holder.stdout.readline().strip() == 'LOCKED'
            fixture.run('boot', success=False)
            assert fixture.values() == before
            assert lock.is_dir()
        finally:
            holder.communicate('\n', timeout=10)
        assert holder.returncode == 0 and not lock.exists()
        fixture.run('boot')
        # Crashes do not let another writer steal the directory lock.
        holder = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert holder.stdout.readline().strip() == 'LOCKED'
        holder.kill(); holder.communicate(timeout=10)
        assert lock.is_dir()
        current = fixture.values()
        fixture.run('disable', success=False)
        assert fixture.values() == current
        # init's post-fs-data rmdir handles this after the next reboot.
        lock.rmdir()
        fixture.run('disable')
        assert fixture.values() == before
    print('PASS: Java and shell mutually exclude writes; crashed holders fail closed')


if __name__ == '__main__':
    main()
