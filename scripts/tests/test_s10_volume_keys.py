#!/usr/bin/env python3
"""Compile the production key hook with Android stubs and exercise press lifecycles."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]

STUBS = {
'android/app/KeyguardManager.java': '''package android.app; public class KeyguardManager {}''',
'android/app/ActivityManager.java': '''package android.app; public class ActivityManager {}''',
'android/net/Uri.java': '''package android.net; public class Uri {}''',
'android/os/Binder.java': '''package android.os; public class Binder { public static int getCallingUid(){return 10000;} }''',
'android/os/Looper.java': '''package android.os; public class Looper { public static Looper getMainLooper(){return new Looper();} }''',
'android/os/Bundle.java': '''package android.os; public class Bundle { public void putBoolean(String x, boolean y){} }''',
'android/os/SystemClock.java': '''package android.os; public class SystemClock { public static long uptimeMillis(){return 10;} }''',
'android/os/PowerManager.java': '''package android.os; public class PowerManager { public boolean interactive=false; public boolean isInteractive(){return interactive;} public void reboot(String s){} }''',
'android/os/Handler.java': '''package android.os; import java.util.*; public class Handler {
 public static final List<Runnable> jobs=new ArrayList<>(); public Handler(Looper l){} public boolean postDelayed(Runnable r,long t){jobs.add(r);return true;}
 public void removeCallbacks(Runnable r){jobs.remove(r);} public static void runAll(){for(Runnable r:new ArrayList<>(jobs)){jobs.remove(r);r.run();}} }''',
'android/view/KeyEvent.java': '''package android.view; public class KeyEvent {
 public static final int KEYCODE_VOLUME_UP=24,KEYCODE_VOLUME_DOWN=25,KEYCODE_MEDIA_NEXT=87,KEYCODE_MEDIA_PREVIOUS=88,ACTION_DOWN=0,ACTION_UP=1;
 private int action,code,repeat; private boolean cancelled;
 public KeyEvent(int action,int code){this.action=action;this.code=code;}
 public KeyEvent(long a,long b,int action,int code,int repeat){this(action,code);}
 public int getKeyCode(){return code;} public int getAction(){return action;} public int getRepeatCount(){return repeat;} public boolean isCanceled(){return cancelled;}
 public KeyEvent repeat(){repeat=1;return this;}
 public KeyEvent cancel(){cancelled=true;return this;} }''',
'android/media/AudioManager.java': '''package android.media; import java.util.*; import java.util.concurrent.Executor; import android.view.KeyEvent;
 public class AudioManager { public interface OnModeChangedListener {void onModeChanged(int mode);}
 public static final int MODE_NORMAL=0,STREAM_MUSIC=3,ADJUST_RAISE=1,ADJUST_LOWER=-1;
 public int mode=0, adjusted=0, media=0; public boolean music=true, failRegister=false, failAdjust=false;
 public final List<OnModeChangedListener> listeners=new ArrayList<>();
 public void addOnModeChangedListener(Executor e,OnModeChangedListener l){if(failRegister)throw new IllegalStateException();listeners.add(l);}
 public void removeOnModeChangedListener(OnModeChangedListener l){listeners.remove(l);}
 public void change(int m){mode=m; for(OnModeChangedListener l:new ArrayList<>(listeners))l.onModeChanged(m);}
 public int getMode(){return mode;} public boolean isMusicActive(){return music;}
 public void adjustStreamVolume(int s,int d,int f){if(failAdjust)throw new IllegalStateException();adjusted+=d;}
 public void dispatchMediaKeyEvent(KeyEvent e){media++;} }''',
'android/content/pm/ActivityInfo.java': '''package android.content.pm; public class ActivityInfo { public String packageName="home"; }''',
'android/content/pm/ResolveInfo.java': '''package android.content.pm; public class ResolveInfo { public ActivityInfo activityInfo=new ActivityInfo(); }''',
'android/content/pm/PackageManager.java': '''package android.content.pm; import android.content.Intent; public class PackageManager {
 public String[] getPackagesForUid(int i){return new String[0];} public ResolveInfo resolveActivity(Intent i,int flags){return new ResolveInfo();} }''',
'android/content/BroadcastReceiver.java': '''package android.content; public abstract class BroadcastReceiver {
 public abstract void onReceive(Context c,Intent i); public int getSentFromUid(){return 0;} }''',
'android/content/IntentFilter.java': '''package android.content; public class IntentFilter { public IntentFilter(String x){} }''',
'android/content/Intent.java': '''package android.content; import android.os.Bundle; public class Intent {
 public static final String ACTION_MAIN="main",CATEGORY_HOME="home"; public static final int FLAG_ACTIVITY_NEW_TASK=1;
 public Intent(){} public Intent(String s){} public String getStringExtra(String s){return null;}
 public Intent addCategory(String s){return this;} public Intent addFlags(int x){return this;}
 public Intent setClassName(String a,String b){return this;} public Intent putExtra(String a,String b){return this;}
 public Intent putExtra(String a,Bundle b){return this;} }''',
'android/content/Context.java': '''package android.content; import android.media.AudioManager; import android.os.*; import android.content.pm.PackageManager;
 public class Context { public static final int RECEIVER_EXPORTED=1; public AudioManager audio=new AudioManager(); public PowerManager power=new PowerManager();
 @SuppressWarnings("unchecked") public <T> T getSystemService(Class<T> c){if(c==AudioManager.class)return (T)audio; if(c==PowerManager.class)return (T)power; return null;}
 public PackageManager getPackageManager(){return new PackageManager();} public void startActivity(Intent i){}
 public void registerReceiver(BroadcastReceiver b,IntentFilter f,String p,Handler h,int flags){} }''',
'com/artisanrom/control/Config.java': '''package com.artisanrom.control; import android.content.Context;
 public class Config { public static String SETTINGS="settings",PACKAGE="package",SETTINGS_FRAGMENT="fragment",BRAND="brand",COMMAND="command";
 public static Context ctx; public static void attach(Context c){ctx=c;} public static Context context(){return ctx;}
 public static Object field(Object o,String s){return ctx;} public static Object invoke(Object a,String b,Class<?>[] c,Object...args){return null;}
 public static void failure(String s,Throwable t){} public static boolean enabled(){return true;} public static boolean on(String s){return true;}
 public static boolean member(String a,String b){return false;} public static String get(String k,String v){return v;}
 public static int integer(String k,int v,int min,int max){return v;} }''',
'com/artisanrom/control/VolumeProbe.java': '''package com.artisanrom.control;
 import android.content.Context; import android.media.AudioManager; import android.view.KeyEvent; import android.os.Handler;
 public class VolumeProbe {
 static void check(boolean b,String m){if(!b)throw new AssertionError(m);}
 static KeyEvent down(){return new KeyEvent(KeyEvent.ACTION_DOWN,KeyEvent.KEYCODE_VOLUME_UP);}
 static KeyEvent up(){return new KeyEvent(KeyEvent.ACTION_UP,KeyEvent.KEYCODE_VOLUME_UP);}
 public static void main(String[] args){
  Context c=new Context(); Config.ctx=c; AudioManager a=c.audio;
  check(SystemHooks.music(null,down()),"down intercepted"); check(a.listeners.size()==1,"listener registered");
  a.change(2); a.change(0); Handler.runAll(); check(a.media==0,"transient mode cancelled hold");
  check(SystemHooks.music(null,up()),"release consumed"); check(a.adjusted==0&&a.listeners.isEmpty(),"no short press after change");
  check(SystemHooks.music(null,down()),"short down"); check(SystemHooks.music(null,up()),"short up");
  check(a.adjusted==1&&a.listeners.isEmpty(),"one adjustment and cleanup");
  check(SystemHooks.music(null,down()),"long down"); Handler.runAll(); check(a.media==2,"one media pair");
  check(SystemHooks.music(null,up()),"long up"); check(a.adjusted==1&&a.listeners.isEmpty(),"long not adjusted");
  check(SystemHooks.music(null,down()),"cancelled down"); check(SystemHooks.music(null,up().cancel()),"cancelled up");
  check(a.adjusted==1&&a.listeners.isEmpty(),"cancelled release no adjustment");
  a.failRegister=true;check(!SystemHooks.music(null,down()),"registration failure delegates");a.failRegister=false;
  check(SystemHooks.music(null,down()),"exception down");a.failAdjust=true;check(SystemHooks.music(null,up()),"exception up consumed");
  check(a.listeners.isEmpty(),"listener removed even when adjustment fails");a.failAdjust=false;
  check(SystemHooks.music(null,down()),"lost up first down");
  check(SystemHooks.music(null,down().repeat()),"repeat keeps original press");check(a.listeners.size()==1,"repeat adds no listener");
  check(SystemHooks.music(null,down()),"new down after lost up");check(a.listeners.size()==1,"stale listener replaced");
  check(SystemHooks.music(null,up()),"new up");check(a.listeners.isEmpty(),"stale recovery cleanup");
  check(SystemHooks.music(null,down()),"unavailable context down");Config.ctx=null;check(SystemHooks.music(null,up()),"up without context consumed");
  check(a.listeners.isEmpty(),"context change cleanup");
  System.out.println("PASS: real volume hook transient mode, short/long/cancelled presses and failure cleanup");
 }
}''',
}


def main():
    with tempfile.TemporaryDirectory(prefix='s10-volume-test-') as directory:
        root = Path(directory)
        files = []
        for relative, source in STUBS.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source)
            files.append(str(path))
        output = root / 'classes'
        output.mkdir()
        source = ROOT / 'packages/ArtisanControl/src/com/artisanrom/control/SystemHooks.java'
        subprocess.run(['javac', '--release', '8', '-d', str(output), str(source), *files], check=True)
        subprocess.run(['java', '-cp', str(output), 'com.artisanrom.control.VolumeProbe'], check=True)


if __name__ == '__main__':
    main()
