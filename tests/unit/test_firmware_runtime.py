"""Run production C++ timekeeping and hourly accounting, not Python replicas."""
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def run_cpp(tmp_path, code, sources=()):
    compiler = shutil.which("clang++") or shutil.which("g++")
    command = [compiler] if compiler else [sys.executable, "-m", "ziglang", "c++"]
    (tmp_path / "Arduino.h").write_text("#pragma once\n#include <cstdint>\nextern uint32_t test_millis; inline uint32_t millis(){return test_millis;}\n")
    (tmp_path / "Wire.h").write_text("#pragma once\nstruct FakeWire { void begin(int,int){} void beginTransmission(int){} void write(int){} int endTransmission(){return 1;} int requestFrom(int,int){return 0;} int read(){return 0;} }; inline FakeWire Wire;\n")
    (tmp_path / "Preferences.h").write_text("#pragma once\n#include <cstdint>\nstruct Preferences { bool begin(const char*,bool){return false;} uint32_t getUInt(const char*,int){return 0;} void putUInt(const char*,uint32_t){} void end(){} };\n")
    # Embedded settimeofday has no host Windows equivalent; only this hardware side effect is stubbed.
    (tmp_path / "sys").mkdir(exist_ok=True)
    (tmp_path / "sys/time.h").write_text("#pragma once\n#include <ctime>\n#ifdef _WIN32\n#include <winsock2.h>\ninline tm* gmtime_r(const time_t* t,tm* out){tm* p=std::gmtime(t); if(p)*out=*p;return p?out:nullptr;}\n#else\n#include_next <sys/time.h>\n#endif\n#define settimeofday test_settimeofday\ninline int test_settimeofday(const timeval*,void*){return 0;}\n")
    unit = tmp_path / "test.cpp"
    unit.write_text(code)
    exe = tmp_path / "test.exe"
    result = subprocess.run(command + ["-std=c++17", "-I", str(tmp_path), "-I", str(ROOT / "firmware/src"), str(unit), *[str(ROOT / s) for s in sources], "-o", str(exe)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    result = subprocess.run([str(exe)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_completed_hour_uses_previous_day_and_sample_mean(tmp_path):
    run_cpp(tmp_path, r'''
#include "hourly_power_accumulator.h"
#include <cassert>
#include <cmath>
int main(){
 ems::HourlyPowerAccumulator acc;
 ems::CompletedPowerHour out;
 assert(!acc.addSample(1789937990, 23, 6, 3, 10, out));
 assert(!acc.addSample(1789937995, 23, 6, 3, 20, out));
 assert(acc.addSample(1789938000, 0, 0, 3, 99, out));
 assert(out.hour == 23 && out.weekday == 6);
 assert(std::fabs(out.mean_kw - 15) < 0.001);
 assert(out.day_completed);
 assert(acc.addSample(1789941600, 1, 0, 3, 31, out));
 assert(out.hour == 0 && out.weekday == 0 && out.mean_kw == 99);
 assert(!out.day_completed);
}
''')


def test_repeated_dst_hour_and_clock_correction_reset(tmp_path):
    run_cpp(tmp_path, r'''
#include "hourly_power_accumulator.h"
#include <cassert>
int main(){
 ems::HourlyPowerAccumulator acc; ems::CompletedPowerHour out;
 assert(!acc.addSample(1792886400,3,6,3,10,out));
 assert(acc.addSample(1792890000,3,6,2,20,out));
 assert(out.mean_kw==10 && !out.day_completed);
 acc.reset();
 assert(!acc.addSample(1793000000,8,0,2,50,out));
 assert(!acc.addSample(1793000005,8,0,2,70,out));
 assert(acc.addSample(1793003600,9,0,2,200,out));
 assert(out.mean_kw==60);
}
''')


def test_ntp_notification_updates_independent_rtc_and_trust(tmp_path):
    run_cpp(tmp_path, r'''
#include "rtc_timekeeper.h"
#include "ntp_sync_handoff.h"
#include <cassert>
uint32_t test_millis=0;
int main(){
 ems::RtcTimekeeper rtc; rtc.begin();
 assert(!rtc.isReliable());
 ems::NtpSyncHandoff handoff;
 handoff.notify(1790000000);
 test_millis=5000;
 assert(handoff.apply(rtc));
 assert(rtc.getSyncSource()==ems::TimeSyncSource::NTP_SYNCED);
 assert(rtc.getCurrentEpoch(8000)==1790000003);
 assert(rtc.isReliable());
 assert(!handoff.apply(rtc));
 handoff.notify(0); assert(!handoff.apply(rtc));
}
''', ["firmware/src/rtc_timekeeper.cpp"])


def test_ct_acquisition_rejects_mode_b_without_relabeling_or_energy_update(tmp_path):
    run_cpp(tmp_path, r'''
#include "power_calc.h"
#include <cassert>
#include <cstring>
uint32_t test_millis=0;
int main(){
 ems::PowerCalculator calc; ems::ThreePhaseMeasurement currents{};
 currents.l1.current_rms_a=20; currents.l2.current_rms_a=20; currents.l3.current_rms_a=20;
 ems::SystemPowerSnapshot snap{};
 assert(calc.tryUpdateCtOnly(currents,0,snap));
 assert(snap.l1.is_estimated && std::strcmp(snap.measurement_method,"estimated_nominal_voltage_pf")==0);
 calc.setMetrologyMode(ems::MetrologyMode::MODE_B_TRUE_RMS);
 snap.total_active_power_kw=123;
 assert(!calc.tryUpdateCtOnly(currents,5000,snap));
 assert(snap.total_active_power_kw==123);
 assert(calc.getMetrologyMode()==ems::MetrologyMode::MODE_B_TRUE_RMS);
 assert(calc.getCumulativeEnergyKWh()==0);
}
''', ["firmware/src/power_calc.cpp"])


def test_hourly_record_does_not_pollute_short_term_momentum_and_clock_reset(tmp_path):
    run_cpp(tmp_path, r'''
#include "edge_forecast.h"
#include <cassert>
int main(){
 EdgeForecaster forecast;
 forecast.updateRecentPowerSample(10); forecast.updateRecentPowerSample(12);
 float velocity=forecast.getVelocityKw();
 forecast.recordHourlyPower(6,23,50);
 assert(forecast.getVelocityKw()==velocity);
 for(int h=0;h<18;++h)forecast.recordHourlyPower(6,h,10);
 forecast.resetObservationHistory();
 assert(forecast.getVelocityKw()==0);
 assert(!forecast.performDailyAdaptation(0));
}
''', ["firmware/src/edge_forecast.cpp"])


def test_rest_request_adds_configured_api_key_and_omits_empty_key(tmp_path):
    run_cpp(tmp_path, r'''
#include "rest_auth.h"
#include <cassert>
#include <string>
struct Request {
 std::string name,value; int calls=0;
 void addHeader(const char* n,const char* v){name=n;value=v;++calls;}
};
int main(){
 Request authenticated;
 ems::addApiKeyHeader(authenticated,"test-key");
 assert(authenticated.calls==1 && authenticated.name=="X-API-Key" && authenticated.value=="test-key");
 Request development;
 ems::addApiKeyHeader(development,""); ems::addApiKeyHeader(development,nullptr);
 assert(development.calls==0);
}
''')


def test_rest_terminal_conflict_drains_head_but_auth_and_transient_errors_retry(tmp_path):
    run_cpp(tmp_path, r'''
#include "telemetry_delivery.h"
#include "ring_buffer.h"
#include <cassert>
#include <initializer_list>
int main(){
 using ems::DeliveryOutcome;
 assert(ems::classifyRestDelivery(201)==DeliveryOutcome::DELIVERED);
 assert(ems::classifyRestDelivery(409)==DeliveryOutcome::TERMINAL_REJECTION);
 for(int code: {-1,401,403,429,500,503})
  assert(ems::classifyRestDelivery(code)==DeliveryOutcome::RETRY);
 ems::RingBuffer<int,4> queue; queue.push(11);queue.push(22);
 int record=0;assert(queue.peek(record) && record==11);
 assert(ems::retireBufferedRecord(queue,record,ems::classifyRestDelivery(409)));
 assert(queue.size()==1 && queue.peek(record) && record==22);
 assert(!ems::retireBufferedRecord(queue,record,ems::classifyRestDelivery(401)));
 assert(queue.size()==1 && queue.peek(record) && record==22);
 assert(!ems::retireBufferedRecord(queue,record,ems::classifyRestDelivery(-1)));
 assert(!ems::retireBufferedRecord(queue,record,ems::classifyRestDelivery(503)));
 assert(ems::retireBufferedRecord(queue,record,ems::classifyRestDelivery(201)));
 assert(queue.isEmpty());
}
''')
