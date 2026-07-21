2026-07-20 프로브 결과 (2026-07-21 최종 확인 추가)

# 카카오 신규 REST API 사전 프로브 (대중교통/도보/자전거 경로조회)

> ## ⛔ 2026-07-21 적용일 최종 확인 결과 — 제휴(파트너십) 전용 API로 확정
>
> 적용 예정일 당일에도 대중교통/도보/자전거 길찾기는 **공개 REST API가 아니라 제휴 계약이
> 필요한 파트너십 API**임이 확정됨. 일반 REST 키로는 호출 불가.
>
> - developers.kakaomobility.com 문서: 여전히 자동차 길찾기 5종만 존재 (대중교통/도보/자전거 없음)
> - 카카오모빌리티 기술제휴팀 공식 답변(데브톡 147260): "제휴가 필요한 내용" →
>   제휴 문의로 안내: https://developers.kakaomobility.com/price/partner
> - 제3자 가이드/데브톡 모두 구체 엔드포인트 URL 없음 (제휴 승인자에게만 제공되는 것으로 추정)
> - 공식 출시 발표·보도자료 없음
>
> **결론: 코드 매핑은 제휴 계약 체결 전까지 불가.** 스캐폴딩(get_travel_time 다중수단 구조,
> placeholder 3함수, KAKAO_MULTIMODAL 플래그, 테스트 6종)은 완료 상태로 대기.
>
> **대안 경로(사용자 결정 필요):**
> 1. 카카오모빌리티 제휴 신청 → 승인 후 스펙 확보 → placeholder 매핑 (기간·비용 불확실)
> 2. 대중교통 슬롯을 ODsay LIVE API로 대체 (사용자 환경에 `korean-transit-route` 스킬 존재,
>    door-to-door 지하철+버스+도보 지원). 자전거는 seoul-bike(따릉이) 등 별도.
> 3. 자동차 단독 유지(현행) + 이 건 보류.

내일(2026-07-21) 예약 태스크 `kakao-newapi-schedule-briefing`이 이 파일을 읽고 시작합니다.

## 결론 (요약)

**3종 신규 API 모두 아직 공개되지 않음.** developers.kakao.com, developers.kakaomobility.com 양쪽 문서 인덱스를 전수 확인한 결과, 대중교통/도보/자전거 경로조회 전용 REST API는 존재하지 않습니다.

| API | 문서 공개 여부 | 비고 |
|---|---|---|
| 대중교통 경로조회 REST API | ❌ 없음 | 카카오모빌리티/카카오디벨로퍼스 어디에도 대중교통(버스·지하철) 경로 전용 API 문서 없음 |
| 도보 경로조회 REST API | ❌ 없음 | "퀵/도보 배송 API"는 배송원 주문 추적용으로, 일반 보행 경로탐색과 무관. 데브톡에는 "Directions API(도보 길찾기)" 사용 권한 신청 게시글이 존재하나, 공식 문서 사이트에는 게시되지 않은 비공개/제휴 API로 추정됨 |
| 자전거 경로조회 REST API | ❌ 없음 | 데브톡 답변("현재 네비게이션은 자동차만 지원, 도보·자전거는 미지원")과 일치 |
| (참고) 정적 지도 조회 API | 미확인 (1차 범위 아님, 이번 프로브에서 별도 조사 안 함) | — |

## 확인 방법

1. `https://developers.kakaomobility.com` (Documentation 홈) 좌측 전체 사이드바 직접 열람 — 브라우저로 스크린샷 확인.
   - 사이드바 구성: `문서 홈 / 길찾기 API / 카카오내비 길찾기 SDK with UI / 카카오내비 길찾기 SDK / 용어집`
   - "길찾기 API" 하위 항목: 자동차 길찾기, 다중 경유지 길찾기, 다중 출발지 길찾기, 다중 목적지 길찾기, 미래 운행 정보 길찾기 — **전부 자동차(내비게이션) 기준**
   - 대중교통/도보/자전거 카테고리 자체가 사이드바에 없음
2. `https://developers.kakao.com` REST API 레퍼런스 전체 API 목록(로그인/톡소셜/톡메시지/톡채널/비즈인증/모먼트/키워드광고/푸시알림/톡캘린더/로컬/Daum검색/사용자편의 API) 확인 — 경로조회 관련 API는 "로컬"(좌표↔주소 변환, 키워드/카테고리 장소 검색)뿐이며 경로(route) 자체를 반환하는 API는 없음
3. WebSearch 교차검증 — "카카오모빌리티 대중교통 경로 API 신규 공개 2026", "카카오모빌리티 도보 자전거 경로 API 신규 출시" 등 검색 → 공식 발표 자료 없음. 데브톡(devtalk.kakao.com) 커뮤니티 게시글에서 "Directions API(도보 길찾기)"가 언급되나 이는 비공개/신청제 API로 보이며 공개 문서 사이트에는 게재되어 있지 않음

## 내일 본작업을 위한 메모

- 내일(7/21) 적용 예정인 API가 아직 공식 문서 사이트에 게시되지 않았다면, 카카오 측 발표 채널(카카오 개발자 공지, 카카오모빌리티 기술 블로그)을 우선 재확인 필요
- 만약 내일 문서가 공개되면, 참고할 기존 패턴(자동차 길찾기 API 계열)의 공통 규격:
  - Host: `https://developers.kakaomobility.com` (길찾기 API), 인증은 REST API 키 기반(`Authorization: KakaoAK ${REST_API_KEY}` 방식, 카카오 표준 규격과 동일할 가능성 높음)
  - 카카오 로컬 API(`dapi.kakao.com`) 좌표 파라미터는 "경도,위도"(x,y) 순서 — 신규 API도 동일 관례를 따를 가능성이 높으나 실제 문서 공개 시 반드시 재확인할 것
- API 키 유효성(200 응답 여부)은 엔드포인트 자체가 아직 확인되지 않아 이번 프로브에서 호출 테스트를 생략함 — 내일 엔드포인트 확정 후 1회 GET 테스트 권장
- 코드 변경 없음, 스펙 메모만 작성함

## 참고 링크

- [Kakaomobility Developers Documentation](https://developers.kakaomobility.com/guide/)
- [카카오디벨로퍼스 REST API 레퍼런스](https://developers.kakao.com/docs/latest/ko/rest-api/reference)
- [카카오모빌리티 Directions API(도보 길찾기), Navigation API 사용신청 - 데브톡](https://devtalk.kakao.com/t/directions-api-navigation-api/147260)
- [카카오 API 중에 도보 경로 안내 API가 있나요? - 데브톡](https://devtalk.kakao.com/t/api-api/142610)
