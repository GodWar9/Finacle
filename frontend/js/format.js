/*
  format.js
  ---------
  Small formatting helpers shared by every render-*.js file. Kept
  separate so each view file only has to worry about its own view,
  not re-implement "how do I print an amount" four times.
*/

var Format = {

  // amount_minor is always an integer count of paise (see doc 05 §1.3
  // — money is never stored as a float). This turns 150000 into
  // "₹1,500.00" for display only.
  money: function (amountMinor, currency) {
    var rupees = amountMinor / 100;
    var symbol = currency === "INR" ? "\u20B9" : (currency || "") + " ";
    return symbol + rupees.toLocaleString("en-IN", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    });
  },

  // Short, readable timestamp for table rows.
  timestamp: function (isoString) {
    var d = new Date(isoString);
    return d.toLocaleString("en-IN", {
      day: "2-digit", month: "short",
      hour: "2-digit", minute: "2-digit"
    });
  },

  // Turns "AMOUNT_MISMATCH" into a CSS-friendly class suffix
  // "amount-mismatch", so the badge CSS classes in components.css
  // can be generated straight from the schema's enum values.
  toCssSuffix: function (enumValue) {
    return enumValue.toLowerCase().replace(/_/g, "-");
  },

  accountLabel: function (accountId) {
    var match = MOCK_DATA.accounts.find(function (a) {
      return a.account_id === accountId;
    });
    return match ? match.account_number + " (" + match.account_type + ")" : accountId;
  }
};
