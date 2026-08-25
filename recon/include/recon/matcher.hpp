#pragma once

#include "recon/types.hpp"
#include <vector>
#include <unordered_map>
#include <mutex>

namespace recon {

class Matcher {
public:
    explicit Matcher(const MatchConfig& config);
    
    std::vector<ReconciliationException> reconcile(
        const std::vector<SettlementRecord>& ledger_records,
        const std::vector<SettlementRecord>& bank_records,
        const std::string& batch_id
    );
    
    void reconcile_parallel(
        const std::vector<SettlementRecord>& ledger_records,
        const std::vector<SettlementRecord>& bank_records,
        const std::string& batch_id,
        std::vector<ReconciliationException>& exceptions
    );

private:
    MatchConfig config_;
    
    void push_exception(
        std::vector<ReconciliationException>& exceptions,
        std::mutex& mutex,
        ExceptionType type,
        const SettlementRecord& bank_row,
        const std::optional<SettlementRecord>& ledger_row = std::nullopt
    );
    
    void mark_untouched_ledger_rows(
        const std::vector<SettlementRecord>& ledger_records,
        const std::vector<SettlementRecord>& bank_records,
        std::vector<ReconciliationException>& exceptions,
        const std::string& batch_id
    );
};

} // namespace recon