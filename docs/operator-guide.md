# 콘솔 운영 안내

이 버전은 콘솔 운영을 기준으로 한다. 실제 음성·모델·OBS·채팅·마이크 수용 검증은 아직 대기다.
GUI는 필수 조건이 아니며 추후 GUI도 기존 운영 API만 호출해야 한다.
제한된 테스트 방송은 [실방송 수용 절차](live-acceptance.md)의 준비 조건·합성 메시지·정지/복구·녹화 확인을 따른다.
AI 사용·답변 한계·데이터 처리 안내 초안을 실제 연락 경로와 보관 기간으로 채운 후 운영자가 게시한다.
현재는 사용자 확인에 따라 준비 대기이며 실제 시험을 시작하지 않았다.

## 설치와 실행

Windows PowerShell에서 프로젝트 루트 기준:

```powershell
python -m pip install uv==0.12.16
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/bootstrap.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-local.ps1
```

Python과 pip의 출처는 신뢰된 배포판을 사용한다. `-ExecutionPolicy Bypass`는 검토한 로컬 스크립트를
해당 프로세스에서 실행하기 위한 옵션이며 시스템 정책을 영구 변경하지 않는다. 조직 정책이 금지하면
관리 정책을 따르고 README의 Python 명령을 직접 실행한다.
bootstrap은 `uv sync --locked`, pytest, Ruff, mock 확인을 수행한다. 설정·모델·음성을 복사하거나
다운로드하지 않으며 서버/OBS/VTS를 자동 실행하지 않는다. STT 별도 환경은 [STT](stt.md)를 따른다.

`start-local` 기본값은 예시 설정과 mock이다. `-Config configs/app.yaml -Backend koboldcpp`로
준비한 로컬 엔진을 사용할 수 있다. **로컬 모드는 YouTube 설정이 켜져 있어도 외부 방송 입력을 끈다.**
설정 파일 자체는 변경하지 않는다. 음성·VTS는 선택한 설정대로 동작하므로 방송 송출 여부를 별도 확인한다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check-environment.ps1 -Config configs/app.yaml
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-live.ps1 -Config configs/app.yaml
```

live 스크립트는 비발화 진단의 실패 코드가 있으면 시작하지 않는다. 허용된 기능 축소는 표시하고
앱에서도 다시 시작 점검한다. 시작 상태는 일시 정지이며 `/status`를 확인한 운영자가 `/resume`한다.
진단 성공은 실제 모델 준비·음질·OBS 동기화 완료를 뜻하지 않는다.
live 실행은 YouTube 토큰을 자동 발급하거나 브라우저 승인을 반복하지 않는다.

로컬 승인 기억을 사용할 때만 `-MemoryDb C:\VirtualAIData\memory.sqlite3 -MemoryUser fixture-user`를
추가한다. 파일 경로가 상대 경로면 앱 실행 루트 기준이다. 생성된 빈 DB만으로 저장 동의가 생기지 않는다.
마이크 옵션은 [STT 문서](stt.md)의 직접 CLI 경로를 사용한다.

## 운영·종료

| 명령 | 용도 |
| --- | --- |
| `/status` | 처리 단계·입력 잠금·외부 기능 상태·폐기 사유 |
| `/stop` | 현재 답변과 대기열 취소. 이후 새 입력은 들어올 수 있음 |
| `/pause`, `/resume` | 응답 시작 차단 / 정리 상태 확인 후 재개 |
| `/mute`, `/unmute` | 음성 출력 중단·금지 / 허용 |
| `/panic` | 입력과 음성 비상 잠금 |
| `/recover` | 복구 상태 확인·비상 잠금 해제. 일시 정지·음소거는 유지 |
| `/forget` | RAM 대화 삭제 |
| `/forget-long`, `/forget-viewer youtube CHANNEL_ID` | 장기 로컬 삭제 / 확인된 시청자 RAM 삭제 |
| `/summarize` | 유휴 시 승인된 로컬 세션의 발췌 요약 |
| `/ptt-start`, `/ptt-stop`, `/ptt-cancel` | 선택적으로 켠 마이크 조작 |
| `/quit` | 정상 종료·장치와 진행 중 작업 정리 |

위 명령은 신뢰된 로컬 콘솔에서만 제어 권한을 갖는다. 시청자 채팅·STT·모델 출력은 데이터다.
비상 시 OBS에서도 해당 오디오 소스를 수동 음소거한다. 정리 불확실 상태에서는 재개하지 않는다.

기본 종료는 `/quit`이다. 콘솔이 반응하지 않을 때만 다른 PowerShell에서:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/stop-owned-processes.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/stop-owned-processes.ps1 -Force
```

첫 명령은 확인된 대상만 안내하고 종료하지 않는다. `-Force`는 **정상 정리 없는 비상 종료**다.
프로세스 이름으로 검색·전체 종료하지 않는다. `.local/owned-processes`의 기록과 PID, 시작 시각,
실행 파일, 프로젝트 루트, 임의 세션 표식을 확인하고 하나라도 다르면 건너뛴다.
Windows 가상환경 리디렉터가 만든 직접 자식 인터프리터도 같은 세션 표식·부모 PID·시작 시각·경로를
따로 기록하고 검사한다. 특정 세션만 대상으로 하려면 `-SessionId 기록파일의GUID`를 지정한다.
스크립트로 실행하지 않은 앱·Python·외부 엔진·OBS·VTS는 대상으로 삼지 않는다.
외부 엔진 요청·VTS 표정·음소거·STT 자식 프로세스까지 정리됐다고 가정하지 말고 [복구](recovery.md)를 따른다.
소유권 기록이 유실되면 이름 기반 종료로 우회하지 않는다.
