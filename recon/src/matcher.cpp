#include "recon/matcher.hpp"
#include <algorithm>
#include <execution>
#include <shared_mutex>

namespace recon {

Matcher::Matcher(const MatchConfig& config) : config_(config) {}

void Matcher::push_exception(
    std::vector<ReconciliationException>& exceptions,
    std::mutex& mutex,
    ExceptionType type,
    const SettlementRecord& bank_row,
    const std::optional<SettlementRecord>& ledger_row
) {
    ReconciliationException exc;
    exc.type = type;
    exc.bank_reference = bank_row.external_reference;
    exc.bank_amount_minor = bank_row.amount_minor;
    exc.detected_at = std::chrono::system_clock::now();
    
    if (ledger_row) {
        exc.ledger_transaction_id = ledger_row->external_reference;
        exc.ledger_amount_minor = ledger_row->amount_minor;
    }
    
    std::lock_guard<std::mutex> lock(mutex);
    exceptions.push_back(std::move(exc));
}

std::vector<ReconciliationException> Matcher::reconcile(
    const std::vector<SettlementRecord>& ledger_records,
    const std::vector<SettlementRecord>& bank_records,
    const std::string& batch_id
) {
    std::vector<ReconciliationException> exceptions;
    std::mutex exceptions_mutex;
    
    reconcile_parallel(ledger_records, bank_records, batch_id, exceptions);
    mark_untouched_ledger_rows(ledger_records, bank_records, exceptions, batch_id);
    
    for (auto& exc : exceptions) {
        exc.batch_id = batch_id;
    }
    
    return exceptions;
}

void Matcher::reconcile_parallel(
    const std::vector<SettlementRecord>& ledger_records,
    const std::vector<SettlementRecord>& bank_records,
    const std::string& batch_id,
    std::vector<ReconciliationException>& exceptions
) {
    std::unordered_map<std::string, const SettlementRecord*> ledger_index;
    ledger_index.reserve(ledger_records.size() * 2);
    
    for (const auto& record : ledger_records) {
        ledger_index[record.external_reference] = &record;
    }
    
    std::mutex exceptions_mutex;
    std::mutex index_mutex;
    std::unordered_map<std::string, bool> matched_ledger;
    matched_ledger.reserve(ledger_records.size() * 2);
    
    #ifdef _OPENMP
    #pragma omp parallel for schedule(dynamic)
    #else
    std::for_each(std::execution::par_unseq, bank_records.begin(), bank_records.end(),
    #endif
        [&](const SettlementRecord& bank_row) {
            const SettlementRecord* ledger_row = nullptr;
            bool found = false;
            
            {
                std::shared_lock<std::shared_mutex> lock(index_mutex);
                auto it = ledger_index.find(bank_row.external_reference);
                if (it != ledger_index.end()) {
                    ledger_row = it->second;
                    found = true;
                }
            }
            
            if (!found) {
                push_exception(exceptions, exceptions_mutex, ExceptionType::MISSING_IN_LEDGER, bank_row);
                return;
            }
            
            {
                std::lock_guard<std::mutex> lock(index_mutex);
                matched_ledger[bank_row.external_reference] = true;
            }
            
            int64_t diff = std::llabs(ledger_row->amount_minor - bank_row.amount_minor);
            if (diff > config_.amount_tolerance_minor) {
                push_exception(exceptions, exceptions_mutex, ExceptionType::AMOUNT_MISMATCH, bank_row, *ledger_row);
                return;
            }
            
            if (ledger_row->status != bank_row.status) {
                push_exception(exceptions, exceptions_mutex, ExceptionType::STATUS_MISMATCH, bank_row, *ledger_row);
            }
        }
    #ifndef _OPENMP
    );
    #endif
    
    for (const auto& bank_row : bank_records) {
        auto it = ledger_index.find(bank_row.external_reference);
        if (it != ledger_index.end()) {
            int64_t count = 0;
            for (const auto& b : bank_records) {
                if (b.external_reference == bank_row.external_reference) count++;
            }
            if (count > 1) {
                push_exception(exceptions, exceptions_mutex, ExceptionType::DUPLICATE, bank_row, *it->second);
            }
        }
    }
}

void Matcher::mark_untouched_ledger_rows(
    const std::vector<SettlementRecord>& ledger_records,
    const std::vector<SettlementRecord>& bank_records,
    std::vector<ReconciliationException>& exceptions,
    const std::string& batch_id
) {
    std::unordered_set<std::string> bank_refs;
    bank_refs.reserve(bank_records.size() * 2);
    for (const auto& r : bank_records) {
        bank_refs.insert(r.external_reference);
    }
    
    std::mutex exceptions_mutex;
    
    for (const auto& ledger_row : ledger_records) {
        if (bank_refs.find(ledger_row.external_reference) == bank_refs.end()) {
            ReconciliationException exc;
            exc.batch_id = batch_id;
            exc.type = ExceptionType::MISSING_IN_BANK_FILE;
            exc.ledger_transaction_id = ledger_row.external_reference;
            exc.bank_reference = ledger_row.external_reference;
            exc.ledger_amount_minor = ledger_row.amount_minor;
            exc.bank_amount_minor = 0;
            exc.detected_at = std::chrono::system_clock::now();
            
            std::lock_guard<std::mutex> lock(exceptions_mutex);
            exceptions.push_back(std::move(exc));
        }
    }
}

} // namespace recon