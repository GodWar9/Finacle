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

// A batch is immutable after completion. Results, completion and events commit
// together; a process killed after commit can safely retry the same batch.
void DbWriter::write_results(const std::string& batch_id, size_t matched_count,
                             const std::vector<ReconciliationException>& exceptions) {
    pqxx::connection conn(conn_string_);
    pqxx::work txn(conn);
    txn.exec_params("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", batch_id);
    const auto existing = txn.exec_params(
        "SELECT status FROM reconciliation_batch_summary WHERE batch_id=$1", batch_id);
    if (!existing.empty()) {
        if (existing[0][0].as<std::string>() != "COMPLETED") {
            throw std::runtime_error("Legacy incomplete batch requires operator review; submit a new batch after review");
        }
        txn.commit();
        return;
    }
    for (const auto& exc : exceptions) {
        const char* ledger_id = nullptr;
        if (exc.ledger_transaction_id && is_plain_uuid(*exc.ledger_transaction_id)) {
            ledger_id = exc.ledger_transaction_id->c_str();
        }
        txn.exec_params(
            "WITH inserted AS (INSERT INTO reconciliation_exceptions "
            "(batch_id, exception_type, ledger_transaction_id, bank_reference, "
            "ledger_amount_minor, bank_amount_minor, resolved, detected_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,false,$7) RETURNING *) "
            "INSERT INTO outbox_events(topic,payload_json) "
            "SELECT 'reconciliation.exception.raised', jsonb_build_object("
            "'exception_id',exception_id,'exception_type',exception_type,"
            "'transaction_id',ledger_transaction_id,'batch_id',batch_id) FROM inserted",
            batch_id, exception_type_to_string(exc.type), ledger_id, exc.bank_reference,
            exc.ledger_amount_minor, exc.bank_amount_minor, to_iso_timestamp(exc.detected_at));
    }
    txn.exec_params(
        "INSERT INTO reconciliation_batch_summary(batch_id,matched_count,exception_count,completed_at,status) "
        "VALUES($1,$2,$3,NOW(),'COMPLETED')", batch_id, matched_count, exceptions.size());
    txn.exec_params(
        "INSERT INTO outbox_events(topic,payload_json) VALUES('reconciliation.batch.completed',"
        "jsonb_build_object('batch_id',$1::text,'matched_count',$2::bigint,'exception_count',$3::bigint))",
        batch_id, matched_count, exceptions.size());
    txn.exec_params(
        "UPDATE settlement_files SET status='COMPLETED',completed_at=NOW(),"
        "processed_count=$2,failed_count=$3,error_message=NULL WHERE batch_id=$1",
        batch_id, matched_count, exceptions.size());
    txn.commit();
}

} // namespace recon
