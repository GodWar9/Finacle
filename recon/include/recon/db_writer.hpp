#pragma once

#include "recon/types.hpp"
#include <pqxx/pqxx>
#include <vector>
#include <string>

namespace recon {

class DbWriter {
public:
    explicit DbWriter(const std::string& conn_string);
    
    void write_exceptions(const std::vector<ReconciliationException>& exceptions);
    void write_batch_summary(const std::string& batch_id, size_t matched_count, size_t exception_count);
    
private:
    std::string conn_string_;
};

} // namespace recon