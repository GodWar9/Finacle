#include "recon/types.hpp"
#include <stdexcept>

namespace recon {

std::string exception_type_to_string(ExceptionType type) {
    switch (type) {
        case ExceptionType::MISSING_IN_LEDGER: return "MISSING_IN_LEDGER";
        case ExceptionType::MISSING_IN_BANK_FILE: return "MISSING_IN_BANK_FILE";
        case ExceptionType::AMOUNT_MISMATCH: return "AMOUNT_MISMATCH";
        case ExceptionType::DUPLICATE: return "DUPLICATE";
        case ExceptionType::STATUS_MISMATCH: return "STATUS_MISMATCH";
    }
    return "UNKNOWN";
}

ExceptionType exception_type_from_string(const std::string& str) {
    if (str == "MISSING_IN_LEDGER") return ExceptionType::MISSING_IN_LEDGER;
    if (str == "MISSING_IN_BANK_FILE") return ExceptionType::MISSING_IN_BANK_FILE;
    if (str == "AMOUNT_MISMATCH") return ExceptionType::AMOUNT_MISMATCH;
    if (str == "DUPLICATE") return ExceptionType::DUPLICATE;
    if (str == "STATUS_MISMATCH") return ExceptionType::STATUS_MISMATCH;
    throw std::invalid_argument("Unknown exception type: " + str);
}

std::string source_to_string(Source source) {
    switch (source) {
        case Source::LEDGER: return "LEDGER";
        case Source::PAYMENT_GATEWAY: return "PAYMENT_GATEWAY";
        case Source::BANK_FILE: return "BANK_FILE";
    }
    return "UNKNOWN";
}

} // namespace recon