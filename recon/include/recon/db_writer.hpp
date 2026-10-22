#pragma once

#include "recon/types.hpp"
#include <pqxx/pqxx>
#include <vector>
#include <string>

namespace recon {

class DbWriter {
public:
    explicit DbWriter(const std::string& conn_string);
    
    void write_results(const std::string& batch_id, size_t matched_count,
                       const std::vector<ReconciliationException>& exceptions);
    
private:
    std::string conn_string_;
};

} // namespace recon