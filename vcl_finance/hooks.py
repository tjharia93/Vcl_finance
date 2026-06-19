app_name = "vcl_finance"
app_title = "VCL Finance"
app_publisher = "Vimit Converters Limited"
app_description = "VCL Finance — petty cash and finance workflows for ERPNext"
app_email = "tanuj.haria@vimit.com"
app_license = "MIT"
app_version = "0.1.0"

# Includes in <head>
# ------------------
app_include_css = "/assets/vcl_finance/css/petty_cash.css"
app_include_js = "/assets/vcl_finance/js/petty_cash.js"

# Website assets — also load on /petty-cash/* portal pages
web_include_css = "/assets/vcl_finance/css/petty_cash.css"
web_include_js = "/assets/vcl_finance/js/petty_cash.js"

# Installation patch list
# -----------------------
# Run after install: seed Petty Cash Category and the 5 vehicle plates.
after_install = "vcl_finance.petty_cash.install.after_install"

# Fixtures
# --------
# Ship the restricted "Petty Cash User" role (Phase 5 login). Filtered so we
# only export this app's own role, not every Role on the site.
fixtures = [
    {"dt": "Role", "filters": [["role_name", "in", ["Petty Cash User"]]]},
]
