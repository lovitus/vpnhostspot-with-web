"""Execute production dashboard helpers on a real JVM, without Android stubs.

Usage: python tests/run_dashboard_behavior.py PATCHED_SOURCE [http|lifecycle|deadline|cache|traffic]
Requires a Kotlin compiler distribution via KOTLIN_LIB, or a cached Gradle 9.x distribution.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

source = Path(sys.argv[1]).resolve()
core = source / 'mobile/src/main/java/be/mygod/vpnhotspot/DashboardCore.kt'
presentation = core.with_name('DashboardPresentation.kt')
page = core.with_name('DashboardPage.kt')
if not core.exists():
    raise SystemExit('FAIL: production DashboardCore.kt is missing (required tested boundary)')
candidates = [Path(os.environ['KOTLIN_LIB'])] if os.environ.get('KOTLIN_LIB') else []
for base in [Path(os.environ.get('GRADLE_USER_HOME', Path.home() / '.gradle')), Path('C:/tmp/vpnhotspot-web-gradle')]:
    candidates.extend(p.parent for p in base.glob('wrapper/dists/gradle-*-bin/*/gradle-*/lib/kotlin-compiler-embeddable-*.jar'))
lib = next((p for p in candidates if list(p.glob('kotlin-compiler-embeddable-*.jar'))), None)
if lib is None:
    raise SystemExit('Set KOTLIN_LIB to the lib directory of a Kotlin compiler distribution')
java = str(Path(os.environ['JAVA_HOME']) / 'bin/java.exe') if os.name == 'nt' else 'java'
stdlib = next(lib.glob('kotlin-stdlib-*.jar'))
coroutines = next(lib.glob('kotlinx-coroutines-core-jvm-*.jar'))
cp = os.pathsep.join(map(str, [stdlib, coroutines]))
scratch = Path.home() / 'AppData/Local/hermes/cache/scratch' if os.name == 'nt' else Path(os.environ.get('TMPDIR', Path.home() / '.cache/hermes/scratch'))
scratch.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='dashboard-jvm-', dir=scratch) as out:
    subprocess.run([java, '-cp', str(lib / '*'), 'org.jetbrains.kotlin.cli.jvm.K2JVMCompiler',
                    '-no-stdlib', '-no-reflect', '-classpath', cp, '-d', out, str(core), str(presentation), str(page),
                    str(Path(__file__).with_name('DashboardBehavior.kt'))], check=True)
    subprocess.run([java, '-Dtest=' + (sys.argv[2] if len(sys.argv) > 2 else 'all'),
                    '-Ddashboard.page.path=' + os.environ.get('DASHBOARD_PAGE_OUTPUT', ''), '-cp',
                    os.pathsep.join([out, cp]), 'be.mygod.vpnhotspot.DashboardBehaviorKt'], check=True)
