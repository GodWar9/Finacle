#include "recon/file_parser.hpp"
#include <fstream>
#include <sstream>
#include <algorithm>
#include <cctype>
#include <charconv>

namespace recon {

static std::string trim(const std::string& str) {
    size_t first = str.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) return "";
    size_t last = str.find_last_not_of(" \t\r\n");
    return str.substr(first, last - first + 1);
}

static std::string substr_safe(std::string_view sv, size_t start, size_t len) {
    if (start >= sv.size()) return "";
    size_t actual_len = std::min(len, sv.size() - start);
    return std::string(sv.substr(start, actual_len));
}

int64_t parse_amount_minor(std::string_view amount_str) {
    std::string trimmed = trim(std::string(amount_str));
    if (trimmed.empty()) return 0;
    
    bool negative = false;
    if (trimmed[0] == '-') {
        negative = true;
        trimmed = trimmed.substr(1);
    } else if (trimmed[0] == '+') {
        trimmed = trimmed.substr(1);
    }
    
    int64_t result = 0;
    for (char c : trimmed) {
        if (std::isdigit(c)) {
            result = result * 10 + (c - '0');
        }
    }
    
    return negative ? -result : result;
}

std::optional<SettlementRecord> parse_line(std::string_view line, const FixedWidthLayout& layout, Source source) {
    if (line.size() < layout.sts_start + layout.sts_len) {
        return std::nullopt;
    }
    
    std::string ref = trim(substr_safe(line, layout.ref_start, layout.ref_len));
    std::string amt_str = substr_safe(line, layout.amt_start, layout.amt_len);
    std::string ccy = trim(substr_safe(line, layout.ccy_start, layout.ccy_len));
    std::string sts = trim(substr_safe(line, layout.sts_start, layout.sts_len));
    
    if (ref.empty()) {
        return std::nullopt;
    }
    
    int64_t amount = parse_amount_minor(amt_str);
    if (amount <= 0) {
        return std::nullopt;
    }
    
    if (ccy.size() != 3) {
        return std::nullopt;
    }
    
    SettlementRecord record;
    record.external_reference = ref;
    record.amount_minor = amount;
    record.currency = ccy;
    record.status = sts.empty() ? "UNKNOWN" : sts;
    record.source = source;
    
    return record;
}

std::vector<SettlementRecord> parse_file(const std::string& filepath, const FixedWidthLayout& layout, Source source) {
    std::vector<SettlementRecord> records;
    std::ifstream file(filepath);
    
    if (!file.is_open()) {
        throw std::runtime_error("Failed to open file: " + filepath);
    }
    
    std::string line;
    size_t line_num = 0;
    while (std::getline(file, line)) {
        line_num++;
        if (line.empty()) continue;
        
        auto record = parse_line(line, layout, source);
        if (record) {
            records.push_back(*record);
        }
    }
    
    return records;
}

} // namespace recon