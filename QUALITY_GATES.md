# 넘기기 전 관문

다섯 칸을 채우기 전에는 과제를 leader에게 넘기지 않는다.

1. 테스트. 과제 대상 저장소의 필수 CI가 실행하는 명령을 그 저장소 루트에서 실행한다. 제품 과제면 제품의 CI와 작업 문서에 있는 명령이다. 이 저장소가 대상이면 아래 명령이고, 이 저장소 CI와 같다.

   ```bash
   python3 -m unittest discover -s scripts -p 'test_*.py'
   python3 -m unittest discover -s tools -p 'test_*.py'
   ```

2. 린트. 명령이 있는 제품은 그 명령을 실행한다. 이 저장소에는 별도 린트 명령이 없으므로 비해당과 이유를 적는다.
3. 화면. 사용자에게 보이는 변경이 있을 때만 확인한다. 없으면 비해당과 이유를 적는다.
4. 문서. 동작이나 절차가 바뀌면 README와 해당 문서를 맞춘다.
5. 보고. 실행한 명령, 종료 코드, 비해당 이유, 확인하지 않은 범위를 과제 폴더의 `REPORT.md`에 적는다.

사무실 자체의 구조 검사는 기준 검사와 따로 실행한다.

```bash
python3 -m unittest discover -s claude-office -p 'test_*.py'
```
