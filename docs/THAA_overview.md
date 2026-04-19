# THAA — Typhoon HIL AI Agent: 전체 동작 흐름

```mermaid
flowchart TB
    %% =====================================================================
    %% LAYER 1 — User entry points
    %% =====================================================================
    subgraph USER["👤 USER INPUT"]
        direction LR
        U1["💬 자연어 목표<br/>(CLI / Web)"]
        U2["📄 .tse 모델 업로드"]
        U3["📋 시나리오 YAML<br/>(scenarios_*.yaml)"]
    end

    %% =====================================================================
    %% LAYER 2 — Entry / config
    %% =====================================================================
    subgraph ENTRY["🚪 ENTRY LAYER"]
        direction LR
        E1["main.py<br/>CLI + FastAPI+SSE"]
        E2["configs/<br/>model.yaml<br/>scenarios.yaml"]
        E3["prompts/<br/>planner.md<br/>analyzer.md"]
    end

    %% =====================================================================
    %% LAYER 3 — LangGraph (control plane)
    %% =====================================================================
    subgraph GRAPH["🧠 LANGGRAPH STATE MACHINE"]
        direction TB
        N1(["1️⃣ load_model<br/>모델 로드 + 신호 발견 + RAG"])
        N2(["2️⃣ plan_tests<br/>YAML 직접로드 OR Claude Planner"])
        N3(["3️⃣ execute_scenario<br/>자극 인가 + 파형 캡처 + Pass/Fail"])
        D1{route_after_exec}
        N4(["4️⃣ analyze_failure<br/>Claude Analyzer 진단"])
        D2{route_after_analysis}
        N5(["5️⃣ apply_fix<br/>XCP / SCADA 보정"])
        N6(["6️⃣ advance_scenario<br/>다음 시나리오로"])
        N7(["7️⃣ generate_report<br/>HTML/Xray 리포트"])

        N1 --> N2 --> N3 --> D1
        D1 -- "PASS + 더있음" --> N6
        D1 -- "FAIL + 재시도<3" --> N4
        D1 -- "끝" --> N7
        N4 --> D2
        D2 -- "fixable + conf>0.5" --> N5
        D2 -- "escalate" --> N6
        N5 --> N3
        N6 --> N3
        N7 --> END(["✅ END"])
    end

    %% =====================================================================
    %% LAYER 4 — Fault templates (stimulus library)
    %% =====================================================================
    subgraph FT["🔧 FAULT TEMPLATES (10종)"]
        direction LR
        FT1["overvoltage<br/>undervoltage"]
        FT2["voltage_sag<br/>voltage_swell<br/>(LVRT/HVRT)"]
        FT3["frequency_deviation<br/>(IEEE 1547 OF/UF)"]
        FT4["short_circuit<br/>open_circuit"]
        FT5["vsm_steady_state<br/>vsm_pref_step<br/>phase_jump<br/>(IEEE 2800 GFM)"]
    end

    %% =====================================================================
    %% LAYER 5 — Tool executors
    %% =====================================================================
    subgraph TOOLS["🛠️ TOOL EXECUTORS (mock + real)"]
        direction LR
        T1["hil_tools<br/>load/start/stop<br/>signal_write/read<br/>capture<br/>fault_inject"]
        T2["xcp_tools<br/>read/write<br/>+ whitelist:<br/>Kp/Ki/Kd/J/D/Kv"]
        T3["rag_tools<br/>Qdrant/Chroma<br/>표준/이력 검색"]
        T4["can_tools<br/>DBC 파싱"]
    end

    %% =====================================================================
    %% LAYER 6 — External / hardware
    %% =====================================================================
    subgraph EXT["⚙️ EXTERNAL SYSTEMS"]
        direction LR
        X1["🔌 Typhoon HIL<br/>(HIL101/606)<br/>또는 VHIL Mock"]
        X2["🚗 ECU<br/>(pyXCP via CAN)"]
        X3["🤖 Claude API<br/>sonnet-4-20250514"]
        X4["📚 IEEE/IEC 표준<br/>레퍼런스 KB"]
    end

    %% =====================================================================
    %% LAYER 7 — Safety / validation
    %% =====================================================================
    subgraph SAFE["🛡️ SAFETY LAYER"]
        direction LR
        S1["validator.py<br/>전압/전류 한도<br/>XCP whitelist<br/>fault count<br/>MAX_HEAL_RETRIES=3"]
    end

    %% =====================================================================
    %% LAYER 8 — Outputs
    %% =====================================================================
    subgraph OUT["📊 OUTPUTS"]
        direction LR
        O1["📈 SSE Event Stream<br/>(실시간 웹 대시보드)"]
        O2["📑 HTML 리포트<br/>(reports/*.html)"]
        O3["📦 Xray JSON<br/>(JIRA 연동)"]
        O4["🐍 pytest 코드<br/>test_project_*/<br/>(코드 생성 파이프라인)"]
    end

    %% =====================================================================
    %% Connections between layers
    %% =====================================================================
    USER --> ENTRY
    ENTRY --> GRAPH
    N3 -.uses.-> FT
    FT -.calls.-> T1
    N1 -.uses.-> T1
    N1 -.uses.-> T3
    N2 -.uses.-> T3
    N2 -.calls.-> X3
    N4 -.calls.-> X3
    N5 -.uses.-> T2
    N3 -.uses.-> T1
    T1 -.runs on.-> X1
    T2 -.connects.-> X2
    T3 -.searches.-> X4
    SAFE -.guards.-> T1
    SAFE -.guards.-> T2
    SAFE -.guards.-> N5
    GRAPH --> OUT
    N7 --> O2
    N7 --> O3
    GRAPH -.streams.-> O1
    ENTRY -.codegen mode.-> O4

    %% =====================================================================
    %% Styling
    %% =====================================================================
    classDef userClass fill:#e3f2fd,stroke:#1565c0,stroke-width:2px,color:#000
    classDef entryClass fill:#fff9c4,stroke:#f9a825,stroke-width:2px,color:#000
    classDef graphClass fill:#c8e6c9,stroke:#2e7d32,stroke-width:3px,color:#000
    classDef ftClass fill:#ffe0b2,stroke:#ef6c00,stroke-width:2px,color:#000
    classDef toolsClass fill:#d1c4e9,stroke:#5e35b1,stroke-width:2px,color:#000
    classDef extClass fill:#ffcdd2,stroke:#c62828,stroke-width:2px,color:#000
    classDef safeClass fill:#f8bbd0,stroke:#ad1457,stroke-width:3px,color:#000
    classDef outClass fill:#b2dfdb,stroke:#00695c,stroke-width:2px,color:#000

    class U1,U2,U3 userClass
    class E1,E2,E3 entryClass
    class N1,N2,N3,N4,N5,N6,N7,D1,D2,END graphClass
    class FT1,FT2,FT3,FT4,FT5 ftClass
    class T1,T2,T3,T4 toolsClass
    class X1,X2,X3,X4 extClass
    class S1 safeClass
    class O1,O2,O3,O4 outClass
```

## 한눈에 보는 흐름

```
USER (자연어 + .tse + YAML)
  └─▶ main.py (CLI/Web)
        └─▶ LangGraph (7 nodes + 3 conditional edges)
              ├─ load_model    → HIL 연결, 신호 발견, RAG 컨텍스트
              ├─ plan_tests    → YAML 직접 OR Claude로 시나리오 생성
              ├─ execute_scenario ┐
              │     ↑            │ 자극 인가 (10 fault templates)
              │     │            ↓ 캡처 + Pass/Fail
              │     │  [route_after_exec]
              │     │     ├─ PASS → advance_scenario → 반복
              │     │     ├─ FAIL → analyze_failure (Claude) → apply_fix → 재실행 (max 3회)
              │     │     └─ DONE → generate_report
              │     └────────────┘
              └─▶ HTML 리포트 + SSE 이벤트 스트림 + (옵션) pytest 코드 생성
```

## 핵심 능력 요약

| 영역 | 구현 |
|------|------|
| **모델 지원** | Typhoon HIL (.tse) — BMS 12S, ESS/EV 충전기, VSM 인버터 검증됨 |
| **표준 커버리지** | IEEE 1547 (전압/주파수/VRT/THD/반도운전), IEEE 2800 (GFM 6영역), IEC 62619, UL 9540, IEC 61851 |
| **자가 치유** | Claude Analyzer 진단 → XCP/SCADA 보정 (J/D/Kv/Kp/Ki/Kd) → 재실행 |
| **안전** | XCP write 화이트리스트, 전압/전류 한도, 결함 주입 카운트, MAX_HEAL_RETRIES=3 |
| **모드** | Real HIL hardware / Virtual HIL / Mock (모두 동일 코드) |
| **출력** | HTML + Xray JSON 리포트 + 실시간 SSE + 자동 pytest 코드 생성 |
| **테스트** | 부모 111개 + GFM 서브프로젝트 28개 = **139개 자동화 테스트** |
