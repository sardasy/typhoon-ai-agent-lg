# Typhoon HIL API Patterns Catalog

> 이 문서는 `CLAUDE.md`의 부담을 덜기 위해 자주 참조하는 `typhoon.test.*` 와 `typhoon.api.hil` 패턴을 별도로 모아둔 레퍼런스입니다.
> Claude Code는 새 코드를 쓰기 전에 관련 섹션을 빠르게 확인하고, 모호한 시그니처는 `WebFetch`로 공식 문서와 교차검증한 뒤 사용합니다.

---

## 1. Import 표준 형태

```python
# 모델 / 컴파일
from typhoon.api.schematic_editor import model

# 시뮬레이션 제어
from typhoon.api import hil

# 측정 / 검증
from typhoon.test import capture
from typhoon.test import signals
from typhoon.test.ranges import around
import typhoon.test.reporting.messages as report
```

❌ 다음 형태는 사용하지 않습니다.

```python
import typhoon.api.hil as hil           # 일관성 깨짐
from typhoon.api import SchematicAPI    # 테스트 코드에서는 model 모듈 사용
```

---

## 2. 모델 로드 / 컴파일

### 2.1 표준 패턴 (conftest의 `setup` fixture 안)

```python
report.report_message("Virtual HIL device is used.")
model.load(MODEL_PATH)
model.compile(conditional_compile=True)            # 변경 없으면 재컴파일 스킵
hil.load_model(COMPILED_MODEL_PATH, vhil_device=True)
```

### 2.2 CPD 경로는 자동 산출

```python
COMPILED_MODEL_PATH = model.get_compiled_model_file(MODEL_PATH)
```

❌ `MODEL_PATH.replace(".tse", ".cpd")` 같은 수동 변환 금지. 빌드 디렉토리 구조가 버전마다 달라집니다.

### 2.3 MODEL_PATH 결정 규칙

```python
from pathlib import Path
import os

FILE_DIR_PATH = Path(__file__).parent
MODEL_PATH = os.path.join(
    FILE_DIR_PATH, "..", "..", "models",
    "<category>", "<model_name>", "<model_name>.tse",
)
```

또는 pytest rootdir 기준 (권장):

```python
@pytest.fixture(scope="session")
def model_path(pytestconfig):
    return pytestconfig.rootpath / "models" / "boost" / "boost.tse"
```

---

## 3. 캡처 (capture)

### 3.1 표준 시퀀스

```python
capture.start_capture(
    duration=1.5,
    rate=10e3,
    signals=["Vout", "Iin"],
)
hil.start_simulation()
hil.wait_sec(0.5)
# (외란 인가 / 파라미터 변경 등)
df = capture.get_capture_results(wait_capture=True)
hil.stop_simulation()
```

핵심 순서: **캡처 설정 → 시뮬 시작 → 대기 → 결과 수집 → 시뮬 정지**.
캡처를 시뮬레이션 시작 후에 설정하면 초기 transient를 놓칩니다.

### 3.2 결과 DataFrame 인덱스는 `timedelta64[ns]`

```python
import pandas as pd

df = capture.get_capture_results(wait_capture=True)

# ❌ TypeError 발생
df.iloc[100]                       # 위치 인덱싱은 동작하지만 시간 의미 사라짐
df.loc[0.001]                      # float 키는 매칭 실패
df[df.index >= 0.5]                # TypeError: Invalid comparison

# ✅ 올바른 방법
df.loc[pd.Timedelta(seconds=0.001)]
df.loc[pd.Timedelta("1ms"):pd.Timedelta("10ms")]
df[df.index >= pd.Timedelta(seconds=0.5)]

# ✅ 헬퍼 (constants.py에 항상 두기)
def ts(seconds: float) -> pd.Timedelta:
    return pd.Timedelta(seconds=seconds)

df[df.index >= ts(0.5)]
```

`signals.assert_is_constant(during=[0.5, 1.5])` 같은 고수준 API는 float를 받아도 내부에서 변환합니다. **수동 슬라이싱 시에만** `pd.Timedelta`를 직접 써야 합니다.

### 3.3 신호명 규칙

- 컴포넌트 이름만 사용: `"Vout"`, `"Iin"`, `"PWM Enable"`
- ❌ `"My model.Vout"`, `"Subsystem1.Vout"` 같은 서브시스템 접두사 금지
- 공백은 그대로 유지: `"PWM Enable"`이 맞고 `"PWM_Enable"`은 다른 신호로 인식됨

---

## 4. 검증 (signals)

### 4.1 `signals.assert_is_constant`

정상상태 일정값 검증.

```python
signals.assert_is_constant(
    response,                              # df['Vout'] 형태의 Series
    around(50, tol_p=0.02),                # 비율 허용오차 (2%)
    during=[0.5, 1.5],                     # [start_s, end_s]
    strictness=0.75,                       # 0.0~1.0, 구간 내 만족 비율
)
```

`tol_p`는 **비율** (0.02 = 2%), `tol_a`는 **절대값**.

### 4.2 `signals.assert_is_first_order`

스텝 응답 1차 추종 검증.

```python
signals.assert_is_first_order(
    response,
    time_constant=0.10,                    # tau (s)
    init_value=50.0,
    final_value=60.0,
    tol=1.0,                               # 절대 허용오차 (V)
    during=[0.5, 1.5],
    time_tol=20e-3,                        # 시간축 허용오차 (s)
)
```

### 4.3 `around()` 사용 가이드

| 케이스 | 표현 | 의미 |
|--------|------|------|
| 비율 허용 | `around(50, tol_p=0.02)` | 49.0 ~ 51.0 (50V ±2%) |
| 절대값 허용 | `around(50, tol_a=1.0)` | 49.0 ~ 51.0 (50V ±1.0V) |
| 비대칭은 직접 구간 사용 | `(low, high)` 튜플 | 일부 API에서 지원 |

`tol_p`와 `tol_a` 동시 지정 금지. 한 쪽만 사용.

### 4.4 Series vs DataFrame

`signals.*` 함수는 **Series** (단일 신호)를 받습니다. `df["Vout"]`처럼 컬럼 추출 후 전달.

---

## 5. 시뮬레이션 제어 (hil)

### 5.1 시작 / 정지

```python
hil.start_simulation()
hil.wait_sec(0.5)              # ❌ time.sleep() 절대 금지
hil.stop_simulation()
```

`time.sleep()`은 호스트 wall clock 기준이므로 시뮬 가속/지연과 어긋납니다. 항상 `hil.wait_sec()`를 사용합니다.

### 5.2 소스 / SCADA 입력 변경

```python
hil.set_source_constant_value("Vin", value=35.0)
hil.set_scada_input_value("PWM Enable", 1.0)
hil.set_scada_input_value("Reference", 50.0)
```

신호명은 `.tse`의 컴포넌트 이름과 정확히 일치해야 합니다.

### 5.3 신호 즉시 읽기 (캡처 없이)

```python
v = hil.read_analog_signal(name="Vout")
```

캡처 결과가 시계열인 반면 `read_analog_signal`은 **현재 순간값**입니다. 정상상태 점검용.

### 5.4 ❌ 금지된 패턴

```python
hil.connect()                  # 테스트에서 불필요
hil.disconnect()               # fixture가 정리 안 함
hil.start_capture(...)         # capture 모듈을 사용할 것
time.sleep(...)                # hil.wait_sec 사용
```

---

## 6. 리포팅

```python
import typhoon.test.reporting.messages as report

report.report_message("Loading model...")
report.report_message("Test step 1: apply Vin disturbance")
```

`print()` 대신 `report.report_message()`를 사용하면 Typhoon HIL 리포트와 Allure에 함께 기록됩니다.

---

## 7. Pytest 통합 패턴

### 7.1 Fixture 계층

```
@pytest.fixture(scope="module")
def setup():
    # 모듈당 한 번: model.load, compile, hil.load_model
    ...

@pytest.fixture()
def reset_parameters():
    # 함수마다: SCADA / 소스 초기값 복원
    ...

@pytest.fixture()
def dut(request):
    # DUT_MODE 환경변수에 따라 HILSimDUT or XCPDUT 반환
    ...
```

❌ `scope="session"` 사용 금지 (모델 컴파일 충돌). `scope="module"`까지만.
❌ `sim_running`, `sim_with_pwm` 같은 통합 fixture 금지. 시뮬 시작/정지는 각 테스트 함수 내부에서 직접.

### 7.2 Parametrize

```python
@pytest.mark.parametrize("vin_dist", [0.80, 1.10])
def test_disturbance_vin(setup, reset_parameters, vin_dist):
    ...
```

조건부 마커는 `pytest.param`을 사용:

```python
@pytest.mark.parametrize("level", [
    pytest.param(50.0, id="nominal"),
    pytest.param(75.0, id="ovp_threshold", marks=pytest.mark.fault_injection),
    pytest.param(100.0, id="hw_only", marks=pytest.mark.hw_required),
])
def test_protection(level, ...): ...
```

### 7.3 마커 규약 (본 프로젝트)

| 마커 | 의미 |
|------|------|
| `vhil_only` | VHIL 모드에서만 실행 |
| `hw_required` | 실 ECU 필요 |
| `fault_injection` | 결함 주입 시나리오 |
| `regression` | CI 회귀 대상 |

`pytest.ini`에 등록:
```ini
[pytest]
markers =
    vhil_only: VHIL only
    hw_required: requires real ECU
    fault_injection: protection logic verification
    regression: included in CI regression
```

---

## 8. Allure 통합

```python
import allure

@allure.feature("DC-DC Boost")
@allure.story("Output regulation")
def test_disturbance_vin(setup, reset_parameters, vin_dist):
    with allure.step("Apply Vin disturbance: {:.2f} pu".format(vin_dist)):
        hil.set_source_constant_value("Vin", value=35.0 * vin_dist)
    
    df = capture.get_capture_results(wait_capture=True)
    allure.attach(
        df.to_csv(),
        name="vout-trace",
        attachment_type=allure.attachment_type.CSV,
    )
```

---

## 9. 자주 마주치는 함정

| 증상 | 원인 | 해결 |
|------|------|------|
| `TypeError: Invalid comparison between dtype=timedelta64[ns] and float` | DataFrame 인덱스에 float 비교 | `pd.Timedelta(seconds=x)` 사용 |
| 컴파일이 매번 도는데 변경 안 했음 | `conditional_compile` 누락 | `model.compile(conditional_compile=True)` |
| 신호가 안 잡힘 | 서브시스템 접두사 사용 | 컴포넌트 이름만으로 |
| `wait_sec`이 너무 빠름/느림 | `time.sleep` 혼용 | 모두 `hil.wait_sec`로 통일 |
| `MODEL_PATH not found` | 상대 경로 + 실행 위치 변동 | `pytestconfig.rootpath` 기반 절대 경로 |
| Windows에서 .py 로딩 시 IDE 크래시 | 한글 주석/문자열 | ASCII-only로 정리 |
| Allure 리포트가 비어있음 | `--alluredir` 누락 | `pytest --alluredir=allure-results` |

---

## 10. 외부 문서 참조 우선순위

1. **본 문서** — 프로젝트 컨벤션 + 검증된 패턴
2. **Context7 (`/websites/pytest_en_stable`)** — pytest 최신 fixture/parametrize/marker 문법
3. **WebFetch — Typhoon HIL 공식 문서**:
   - `https://www.typhoon-hil.com/documentation/typhoon-hil-api-documentation/hil_api.html`
   - `https://www.typhoon-hil.com/documentation/typhoon-hil-api-documentation/test_api.html`
   - `https://www.typhoon-hil.com/documentation/typhoon-hil-api-documentation/schematic_editor_api.html`
4. **레퍼런스 `.tse` 파일** — 터미널명·프로퍼티 의심스러우면 실제 모델을 열어 확인

외부 문서가 본 문서와 충돌하면 **본 문서가 우선**합니다 (Typhoon HIL 테스트 환경 특화 규칙은 일반 API 문서에 안 나오는 경우가 많음).

---

## 11. 변경 이력

- v1.0: capture/signals/hil 표준 패턴, Allure 통합, 함정 카탈로그 정리
