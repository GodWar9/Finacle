#pragma once

#include <cstdint>
#include <string>
#include <optional>
#include <vector>
#include <chrono>

namespace recon {

enum class Source { LEDGER, PAYMENT_GATEWAY, BANK_FILE };

struct SettlementRecord {
    std::string external_reference;
    int64_t amount_minor;
    std::string currency;
    std::string status;
    Source source;
};

enum class ExceptionType {
    MISSING_IN_LEDGER,
    MISSING_IN_BANK_FILE,
    AMOUNT_MISMATCH,
    DUPLICATE,
    STATUS_MISMATCH
};

struct ReconciliationException {
    std::string batch_id;
    ExceptionType type;
    std::optional<std::string> ledger_transaction_id;
    std::string bank_reference;
    int64_t ledger_amount_minor{0};
    int64_t bank_amount_minor{0};
    std::chrono::system_clock::time_point detected_at;
};

struct MatchConfig {
    int64_t amount_tolerance_minor = 0;
    bool case_sensitive_reference = true;
};

std::string exception_type_to_string(ExceptionType type);
ExceptionType exception_type_from_string(const std::string& str);
std::string source_to_string(Source source);

} // namespace recon