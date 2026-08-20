## Why

Hệ thống mới đã đạt route-parity nhưng còn dấu hiệu behavior drift ở execution path và council/risk observability so với `botux-backend/`:
- gate freshness/price-drift của auto-trading chưa được enforce đầy đủ như legacy.
- log vote/risk hiện còn khó truy vết theo signal-level khi điều tra reject/veto.
- một số bề mặt `legacy**` vẫn là hotspot khó maintain.
- vẫn còn JSON field chứa dữ liệu có thể tách cột rõ nghĩa để query/audit tốt hơn.

## What Changes

- Hardening execution parity cho auto-trading, tập trung vào pre-execution gates và telemetry reject reasons.
- Chuẩn hóa logging council/risk thành structured audit trail theo từng signal, vote, failure.
- Rationalize naming/structure của các module `legacy**` thành nhóm `compat/*` có facade ổn định.
- Rà soát schema để flatten các field truy vấn thường xuyên khỏi JSON; giới hạn JSON cho payload biến thiên.
- Đánh giá khả năng reset migration chain theo tiêu chí rủi ro + cutover plan, không làm "big-bang" khi chưa có dry-run proof.

## Capabilities

### New Capabilities
- `autotrading-parity-hardening`: enforce các execution gates cần thiết để giảm drift giữa legacy và refactor.
- `schema-flattening-governance`: định nghĩa tiêu chí field nào cần flatten, field nào giữ JSON.
- `legacy-module-rationalization`: tái cấu trúc naming và module boundaries cho compatibility surfaces.

### Modified Capabilities
- Nâng cấp `council/risk` observability để forensic/debug nhanh và rõ nguyên nhân reject/veto.

## Impact

- Affected code: `src/app/services/execution/service.py`, `src/app/usecases/submit_order.py`, `src/api/routers/legacy_api_extra.py`, `src/app/services/scan/service.py`, runtime config service.
- Affected tests: parity/smoke/runtime control/usecase tests.
- DB impact: có thể phát sinh migration flatten field theo từng batch nhỏ, có rollback chứng minh được.
- Không thay đổi contract API public theo hướng breaking.
