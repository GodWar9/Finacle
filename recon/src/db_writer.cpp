#include "recon/db_writer.hpp"
#include <pqxx/pqxx>
#include <sstream>
#include <iostream>
#include <ctime>

namespace recon {

std::string to_iso_timestamp(std::chrono::system_clock::time_point tp) {
    std::time_t t = std::chrono::system_clock::to_time_t(tp);
    std::tm tm{};
    gmtime_r(&t, &tm);
    char buf[32];
    std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &tm);
    return buf;
}

// Returns a pointer to a valid UUID string, or nullptr when the reference is
// a non-UUID bank reference (e.g. a truncated 20-char ref). Used so the
// ledger_transaction_id FK column stays NULL for references that are not real
// ledger transaction ids.
static bool is_plain_uuid(const std::string& s) {
    if (s.size() != 36) return false;
    for (size_t i = 0; i < s.size(); ++i) {
        const char c = s[i];
        const bool is_hex = (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
        if (i == 8 || i == 13 || i == 18 || i == 23) {
            if (c != '-') return false;
        } else if (!is_hex) {
            return false;
        }
    }
    return true;
}

DbWriter::DbWriter(const std::string& conn_string) : conn_string_(conn_string) {}

void DbWriter::write_exceptions(const std::vector<ReconciliationException>& exceptions) {
    if (exceptions.empty()) return;

    pqxx::connection conn(conn_string_);
    pqxx::work txn(conn);

    for (const auto& exc : exceptions) {
        const char* ledger_id = nullptr;
        if (exc.ledger_transaction_id && is_plain_uuid(exc.ledger_transaction_id.value())) {
            ledger_id = exc.ledger_transaction_id->c_str();
        }
        txn.exec_params(
            "INSERT INTO reconciliation_exceptions "
            "(batch_id, exception_type, ledger_transaction_id, bank_reference, "
            " ledger_amount_minor, bank_amount_minor, resolved, detected_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            exc.batch_id,
            exception_type_to_string(exc.type),
            ledger_id,
            exc.bank_reference,
            exc.ledger_amount_minor,
            exc.bank_amount_minor,
            false,
            to_iso_timestamp(exc.detected_at)
        );
    }

    txn.commit();
}

void DbWriter::write_batch_summary(const std::string& batch_id, size_t matched_count, size_t exception_count) {
    pqxx::connection conn(conn_string_);
    pqxx::work txn(conn);

    txn.exec_params(
        "INSERT INTO reconciliation_batch_summary (batch_id, matched_count, exception_count, completed_at) VALUES ($1, $2, $3, NOW())",
        batch_id, matched_count, exception_count
    );

    txn.commit();
}

} // namespace recon
