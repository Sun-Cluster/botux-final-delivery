## Context

Refactor hiện tại giữ được phần lớn compatibility endpoints nhưng execution/gate telemetry chưa đủ chiều sâu để truy ngược behavior drift như legacy `trade_executor`.

Các điểm nóng:
- pre-execution guard thiếu freshness/price-drift gate trong shared execution path.
- vote/risk diagnostics còn phân tán giữa pipeline log và record tổng hợp.
- module naming `legacy_*` còn phản ánh trạng thái tạm thời, không còn phù hợp dài hạn.
- JSON-heavy fields làm giảm khả năng query/alert/index.

## Goals / Non-Goals

**Goals**
- Khóa chặt gate semantics cốt lõi cho auto-trading.
- Tạo audit trail rõ ràng cho council vote + risk failure.
- Chuẩn hóa structure compatibility modules để maintain dễ hơn.
- Flatten schema có chọn lọc, ưu tiên field hay query/filter/report.

**Non-Goals**
- Không thay đổi chiến lược trading logic theo hướng product mới.
- Không reset migration toàn bộ ngay lập tức nếu chưa có cutover rehearsal.

## Decisions

1. Enforce missing execution gates trong `ExecutionService` thay vì thêm logic phân tán theo route.
2. Log council/risk chi tiết ngay tại usecase submit path để cùng transaction scope với decision persistence.
3. Đổi tên `legacy**` theo hướng `compat/*` nhưng giữ facade/import ổn định trong giai đoạn chuyển tiếp.
4. Flatten schema theo batch nhỏ; mỗi batch có migration + backfill + rollback SQL proof.

## Risks / Trade-offs

- Gate cứng hơn có thể làm giảm số lệnh executed trong ngắn hạn.
  - Mitigation: config-driven thresholds, quan sát bằng runtime metrics.
- Refactor naming có nguy cơ break import/tests.
  - Mitigation: giữ shim/facade trong 1-2 batch trước khi xóa.
- Flatten quá nhanh có thể tăng migration risk.
  - Mitigation: ưu tiên table/field có high-read trước, benchmark query plan sau mỗi batch.

## Migration Plan

1. Phase A: parity bugfix + telemetry hardening (không breaking).
2. Phase B: module rationalization (`legacy**` -> `compat/*`) với facade giữ nguyên.
3. Phase C: schema flatten batch-1 (gate/vote/risk/reporting fields).
4. Phase D: review migration chain; chỉ reset-from-zero khi có full staging rehearsal thành công.

Rollback:
- từng phase độc lập, revert theo batch commit/migration.
