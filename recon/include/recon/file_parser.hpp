#pragma once

#include "recon/types.hpp"
#include <string_view>
#include <optional>
#include <vector>

namespace recon {

struct FixedWidthLayout {
    size_t ref_start = 0,  ref_len = 20;
    size_t amt_start = 20, amt_len = 13;
    size_t ccy_start = 33, ccy_len = 3;
    size_t sts_start = 36, sts_len = 7;
};

std::optional<SettlementRecord> parse_line(std::string_view line, const FixedWidthLayout& layout, Source source);
std::vector<SettlementRecord> parse_file(const std::string& filepath, const FixedWidthLayout& layout, Source source);
int64_t parse_amount_minor(std::string_view amount_str);

} // namespace recon