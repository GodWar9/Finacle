#include "recon/db_writer.hpp"
#include <pqxx/pqxx>
#include <sstream>
#include <iostream>

namespace recon {

DbWriter::DbWriter(const std::string& conn_string) : conn_string_(conn_string) {}

void DbWriter::write_exceptions(const std::vector<ReconciliationException>& exceptions) {
    if (exceptions.empty()) return;

    pqxx::connection conn(conn_string_);
    pqxx::work txn(conn);

    pqxx::stream_to stream = pqxx::stream_to::table(
        txn,
        {"reconciliation_exceptions"},
        {"batch_id", "exception_type", "ledger_transaction_id", "bank_reference",
         "ledger_amount_minor", "bank_amount_minor", "resolved", "detected_at"}
    );

    for (const auto& exc : exceptions) {
        stream << std::make_tuple(
            exc.batch_id,
            exception_type_to_string(exc.type),
            exc.ledger_transaction_id,
            exc.bank_reference,
            exc.ledger_amount_minor,
            exc.bank_amount_minor,
            false,
            std::chrono::system_clock::to_time_t(exc.detected_at)
        );
    }

    stream.complete();
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
