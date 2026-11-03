"""Well-known tech employers in India: canonical name + common short forms and misspellings.

Used by normalize.py (autocorrect / cleanup) and the Jobs page's "Company careers" directory.
Format: "Canonical Name|alias, alias". Aliases are matched after lower-casing and dropping legal suffixes.
"""

TOP_INDIA = [
    # Big Tech and global product companies with large India centres
    "Google|alphabet, google india", "Microsoft|msft, microsoft idc", "Amazon|amzn, amazon development centre, a2z",
    "Amazon Web Services|aws", "Apple", "Meta|facebook, fb", "Netflix", "NVIDIA|nvidia graphics", "OpenAI",
    "Anthropic", "Adobe|adobe systems", "Salesforce|sfdc", "Oracle|oracle financial services, ofss", "SAP|sap labs",
    "IBM|ibm india", "Intel", "Qualcomm", "Cisco|cisco systems", "AMD|advanced micro devices", "Samsung|samsung r&d, srib",
    "Uber", "LinkedIn", "Atlassian", "Intuit", "PayPal", "Visa", "Mastercard", "American Express|amex",
    "Walmart Global Tech|walmart, walmart labs", "Target", "Goldman Sachs|gs", "JPMorgan Chase|jp morgan, jpmc, jpmorgan",
    "Morgan Stanley", "Deutsche Bank", "Barclays", "Citi|citibank, citigroup", "HSBC", "Wells Fargo", "UBS",
    "BNY|bny mellon, bank of new york mellon", "State Street", "Fidelity Investments|fidelity, fmr", "BlackRock",
    "Nomura", "Standard Chartered", "Texas Instruments|ti", "Micron", "Western Digital|wdc", "Seagate", "NetApp",
    "Nutanix", "VMware", "Broadcom", "Dell Technologies|dell", "HP|hewlett packard, hp inc", "Hewlett Packard Enterprise|hpe",
    "Siemens", "Bosch|robert bosch, bosch global software", "Honeywell", "GE HealthCare|ge healthcare", "Philips",
    "Shell", "Thomson Reuters", "Autodesk", "Workday", "ServiceNow", "Splunk", "Palo Alto Networks", "Zscaler",
    "Okta", "Databricks", "Snowflake", "MongoDB", "Confluent", "Elastic", "Twilio", "Stripe", "Rubrik", "Cohesity",
    "Druva", "Commvault", "Nasdaq", "Expedia|expedia group", "Booking.com|booking", "Agoda", "Grab", "Canva",
    "Arista Networks|arista", "Juniper Networks|juniper", "Synopsys", "Cadence|cadence design systems",
    "Infineon", "NXP|nxp semiconductors", "Analog Devices|adi", "Marvell", "MathWorks", "Ericsson", "Nokia",
    "Wipro GE|wipro ge healthcare", "Electronic Arts|ea", "Ubisoft", "Rockstar Games|rockstar", "Zynga",
    "Chubb", "Optum|unitedhealth group, uhg", "McKinsey|mckinsey & company", "Boston Consulting Group|bcg",
    "Bain|bain & company", "Deloitte", "PwC|pricewaterhousecoopers", "EY|ernst & young", "KPMG", "Accenture",
    "Capgemini", "Cognizant|cts", "Thoughtworks", "Publicis Sapient|sapient", "EPAM|epam systems", "Globant",
    "Gartner", "Experian", "NielsenIQ|niq", "Ola Electric", "Rippling", "Postman", "BrowserStack", "Freshworks|freshdesk",
    "Zoho|zoho corp", "Chargebee", "Razorpay", "PhonePe", "Paytm|one97, one97 communications", "Flipkart",
    "Myntra", "Swiggy", "Zomato|eternal", "Blinkit", "Zepto", "Meesho", "CRED|dreamplug", "Groww", "Zerodha",
    "Upstox", "Dream11|dream sports", "MPL|mobile premier league", "Nykaa|fsn e-commerce", "Ola|ani technologies",
    "Rapido", "Urban Company|urbanclap", "Lenskart", "Delhivery", "Shiprocket", "Udaan", "ShareChat|mohalla tech",
    "InMobi", "Glance", "Unacademy", "upGrad", "Physics Wallah|pw", "BYJU'S|byjus, think and learn",
    "PolicyBazaar|pb fintech", "Acko", "Digit Insurance|go digit", "Navi", "Slice", "Jupiter", "Fi Money|fi",
    "BharatPe", "Pine Labs", "Juspay", "Cashfree|cashfree payments", "Khatabook", "OYO|oravel", "MakeMyTrip|mmt",
    "Ixigo", "Cleartrip", "BigBasket", "JioMart", "Jio Platforms|jio, reliance jio", "Reliance Industries|ril",
    "Airtel|bharti airtel", "Tata Consultancy Services|tcs, tata consultancy", "Infosys|infy", "Wipro",
    "HCLTech|hcl, hcl technologies", "Tech Mahindra|techm", "LTIMindtree|lti, mindtree, larsen & toubro infotech",
    "L&T Technology Services|ltts", "Persistent Systems|persistent", "Mphasis", "Coforge|niit technologies",
    "Hexaware", "Zensar", "Birlasoft", "Cyient", "KPIT|kpit technologies", "Tata Elxsi", "Sasken", "Happiest Minds",
    "Mastek", "Sonata Software|sonata", "Tata Digital|tata neu", "Tata Communications", "Tata 1mg|1mg",
    "Infoedge|info edge, naukri", "Zeta", "Mindtickle", "Whatfix", "Icertis", "Innovaccer", "HighRadius",
    "Darwinbox", "LeadSquared", "CleverTap", "MoEngage", "WebEngage", "Gupshup", "Yellow.ai|yellow messenger",
    "Haptik", "Sprinklr", "Druva", "Hasura", "Appsmith", "DevRev", "Atlan", "Sarvam AI|sarvam", "Krutrim",
    "Fractal|fractal analytics", "Mu Sigma", "Tiger Analytics", "LatentView|latentview analytics", "Sigmoid",
    "Tredence", "Quantiphi", "Course5|course5 intelligence", "Ather Energy|ather", "Zetwerk", "Licious", "Spinny",
    "Cars24", "CarDekho|girnar", "Pocket FM", "STAGE", "Hevo Data|hevo", "Chalo", "Porter", "boAt|imagine marketing",
    "Games24x7", "Nazara|nazara technologies", "Tower Research Capital|tower research", "Graviton Research Capital|graviton",
    "Squarepoint Capital|squarepoint", "Jump Trading", "Optiver", "IMC Trading|imc", "Quadeye", "WorldQuant",
    "D. E. Shaw|de shaw, d e shaw, deshaw", "Arcesium", "Tekion", "Media.net|medianet", "Directi|radix",
    "Sprinto", "Observe.AI|observeai", "Uniphore", "Exotel", "Kissflow", "Zuora", "Gojek", "Uber Freight",
    "Coinbase", "CoinDCX", "CoinSwitch", "Polygon|polygon labs", "Turing", "HackerRank", "LambdaTest", "Cloudflare",
    "Datadog", "GitLab", "Harness", "Netskope", "Pure Storage", "Samsara", "Toast", "Fivetran", "New Relic",
    "Sumo Logic", "Fastly", "Contentstack", "Mixpanel", "Notion", "Amplitude", "Figma", "Vercel", "ClickHouse",
    "Redis", "Temporal", "Yugabyte|yugabytedb", "Cockroach Labs|cockroachdb", "Grafana Labs|grafana", "Starburst",
    "Airbnb", "Sophos", "Outreach", "Anyscale", "Composio", "Continental", "Ola Krutrim",
]


def parsed():
    """[(canonical, [aliases...])] with duplicates removed (first entry wins)."""
    out, seen = [], set()
    for line in TOP_INDIA:
        name, _, rest = line.partition("|")
        name = name.strip()
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append((name, [a.strip() for a in rest.split(",") if a.strip()]))
    return out
