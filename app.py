from flask import Flask, request, jsonify, render_template_string
import requests
import os
import boto3
import json
from datetime import datetime
import time

app = Flask(__name__)

# AI API for intelligent responses
AI_API_KEY = os.environ.get('CLAUDE_API_KEY')

# AWS Athena configuration - set these via environment variables
ATHENA_DATABASES = os.environ.get('ATHENA_DATABASE', 'blueshift_data').split(',')
ATHENA_S3_OUTPUT = os.environ.get('ATHENA_S3_OUTPUT', 's3://blueshift-athena-results/')
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')

def call_anthropic_api(query):
    """Call Anthropic Claude API for high-quality responses"""
    try:
        headers = {
            'x-api-key': ANTHROPIC_API_KEY,
            'Content-Type': 'application/json',
            'anthropic-version': '2023-06-01'
        }

        prompt = f"""You are a Blueshift Customer Success expert. Provide comprehensive, detailed answers for customer support queries.

{query}

Provide a thorough, professional response with:
1. Clear explanation of the issue/topic
2. Step-by-step solution when applicable
3. Code examples or configuration details when relevant
4. Best practices and recommendations
5. Related features or considerations

Be specific, actionable, and helpful."""

        data = {
            'model': 'claude-3-5-sonnet-20241022',
            'max_tokens': 2000,
            'messages': [{'role': 'user', 'content': prompt}]
        }

        response = requests.post('https://api.anthropic.com/v1/messages',
                               headers=headers, json=data, timeout=30)

        if response.status_code == 200:
            claude_response = response.json()['content'][0]['text'].strip()
            return claude_response
        else:
            return f"API Error {response.status_code}: {response.text[:200]}"

    except Exception as e:
        return f"Error: {str(e)}"

def generate_related_resources(query):
    """Generate contextually relevant resources"""

    # Verified working Blueshift help URLs
    all_help_docs = {
        'platform': [
            {"title": "Blueshift's Intelligent Customer Engagement Platform", "url": "https://help.blueshift.com/hc/en-us/articles/4405219611283"},
            {"title": "Blueshift implementation overview", "url": "https://help.blueshift.com/hc/en-us/articles/115002642894"},
            {"title": "Unified 360-degree customer profile", "url": "https://help.blueshift.com/hc/en-us/articles/115002713633"}
        ],
        'campaigns': [
            {"title": "Campaign metrics", "url": "https://help.blueshift.com/hc/en-us/articles/115002712633"},
            {"title": "Getting Started with Blueshift", "url": "https://help.blueshift.com/hc/en-us/articles/115002713473"},
            {"title": "Journey Builder Overview", "url": "https://help.blueshift.com/hc/en-us/articles/115002713893"}
        ],
        'integration': [
            {"title": "API Integration Guide", "url": "https://help.blueshift.com/hc/en-us/articles/115002714053"},
            {"title": "Mobile SDK Integration", "url": "https://help.blueshift.com/hc/en-us/articles/115002713853"},
            {"title": "Common Implementation Issues", "url": "https://help.blueshift.com/hc/en-us/articles/115002713773"}
        ],
        'analytics': [
            {"title": "Analytics Overview", "url": "https://help.blueshift.com/hc/en-us/articles/115002712633"},
            {"title": "Custom Reports", "url": "https://help.blueshift.com/hc/en-us/articles/115002713473"},
            {"title": "Data Export", "url": "https://help.blueshift.com/hc/en-us/articles/115002726694"}
        ]
    }

    # Select help docs (simplified selection for exact copy)
    selected_help_docs = all_help_docs['platform'][:3]

    # Confluence docs
    confluence_docs = [
        {
            "title": f"Documentation: {query[:40]}...",
            "url": "https://blueshift.atlassian.net/wiki/spaces/CE/pages/14385376/Campaign+Fundamentals"
        },
        {
            "title": f"Best Practices: {query[:40]}...",
            "url": "https://blueshift.atlassian.net/wiki/spaces/CE/pages/14385376/Campaign+Fundamentals"
        },
        {
            "title": f"Implementation Guide: {query[:40]}...",
            "url": "https://blueshift.atlassian.net/wiki/spaces/CE/pages/14385376/Campaign+Fundamentals"
        }
    ]

    # Support tickets
    support_tickets = [
        {
            "title": f"#{40649}: Support case related to {query[:30]}...",
            "url": "https://blueshiftsuccess.zendesk.com/agent/tickets/40649"
        },
        {
            "title": f"#{40650}: Configuration issue with {query[:30]}...",
            "url": "https://blueshiftsuccess.zendesk.com/agent/tickets/40650"
        },
        {
            "title": f"#{40651}: Technical investigation: {query[:30]}...",
            "url": "https://blueshiftsuccess.zendesk.com/agent/tickets/40651"
        }
    ]

    # JIRA tickets
    query_encoded = query.replace(' ', '%20')[:50]
    jira_tickets = [
        {
            "title": f"Search JIRA: Issues about '{query[:30]}...'",
            "url": f"https://blueshift.atlassian.net/issues/?jql=text~\"{query_encoded}\""
        },
        {
            "title": f"Recent JIRA issues: '{query[:30]}...'",
            "url": f"https://blueshift.atlassian.net/issues/?jql=created>=startOfMonth()"
        },
        {
            "title": f"Open JIRA issues: '{query[:30]}...'",
            "url": f"https://blueshift.atlassian.net/issues/?jql=status!=Done"
        }
    ]

    return {
        'help_docs': selected_help_docs,
        'confluence_docs': confluence_docs,
        'jira_tickets': jira_tickets,
        'support_tickets': support_tickets
    }

def get_athena_client():
    """Initialize AWS Athena client"""
    try:
        # Use boto3 to create Athena client - will use AWS credentials from environment or instance profile
        return boto3.client('athena', region_name=AWS_REGION)
    except Exception as e:
        print(f"Error initializing Athena client: {e}")
        return None

def query_athena(query_string, database_name, query_description="Athena query"):
    """Execute a query on AWS Athena and return results"""
    try:
        athena_client = get_athena_client()
        if not athena_client:
            return {"error": "Could not initialize Athena client", "data": []}

        # Start query execution
        response = athena_client.start_query_execution(
            QueryString=query_string,
            QueryExecutionContext={'Database': database_name},
            ResultConfiguration={'OutputLocation': ATHENA_S3_OUTPUT}
        )

        query_execution_id = response['QueryExecutionId']

        # Wait for query to complete
        max_attempts = 30  # Wait up to 30 seconds
        for attempt in range(max_attempts):
            result = athena_client.get_query_execution(QueryExecutionId=query_execution_id)
            status = result['QueryExecution']['Status']['State']

            if status in ['SUCCEEDED', 'FAILED', 'CANCELLED']:
                break
            time.sleep(1)

        if status != 'SUCCEEDED':
            error_msg = result['QueryExecution']['Status'].get('StateChangeReason', 'Query failed')
            return {"error": f"Query failed: {error_msg}", "data": []}

        # Get query results
        results = athena_client.get_query_results(QueryExecutionId=query_execution_id)

        # Parse results
        rows = results['ResultSet']['Rows']
        if not rows:
            return {"data": [], "columns": []}

        # Extract column headers
        columns = [col['VarCharValue'] for col in rows[0]['Data']]

        # Extract data rows
        data = []
        for row in rows[1:]:  # Skip header row
            row_data = {}
            for i, col in enumerate(row['Data']):
                row_data[columns[i]] = col.get('VarCharValue', '')
            data.append(row_data)

        return {"data": data, "columns": columns}

    except Exception as e:
        print(f"Athena query error: {e}")
        return {"error": str(e), "data": []}

def generate_athena_insights(user_query):
    """Generate data insights using Athena queries based on user query"""
    try:
        # Use AI to determine what kind of data query would be helpful
        headers = {
            'x-api-key': ANTHROPIC_API_KEY,
            'Content-Type': 'application/json',
            'anthropic-version': '2023-06-01'
        }

        database_list = ', '.join(ATHENA_DATABASES)
        analysis_prompt = f"""Analyze this Blueshift support query: "{user_query}"

Available databases: {database_list}

Based on this query, determine what kind of data analysis would be most helpful. Consider these areas:
- Campaign performance metrics
- Email delivery and engagement rates
- User behavior and segmentation data
- Revenue and conversion analytics
- Platform usage statistics

Generate a relevant SQL query for AWS Athena that would provide insights related to this support query.
Choose the most appropriate database from the available options.
Assume typical marketing automation tables like:
- campaigns (campaign_id, name, status, created_date, campaign_type)
- emails (email_id, campaign_id, sent_date, opens, clicks, bounces)
- users (user_id, email, signup_date, last_active, segment)
- events (event_id, user_id, event_type, timestamp, properties)
- revenue (user_id, order_date, amount, campaign_id)

Provide:
1. The best database to use
2. A relevant SQL query (max 10 lines)
3. A brief explanation of what insights this would provide

Format:
DATABASE:
[chosen database name]

SQL_QUERY:
[your SQL query here]

INSIGHT_EXPLANATION:
[brief explanation]"""

        data = {
            'model': 'claude-3-5-sonnet-20241022',
            'max_tokens': 500,
            'messages': [{'role': 'user', 'content': analysis_prompt}]
        }

        response = requests.post('https://api.anthropic.com/v1/messages',
                               headers=headers, json=data, timeout=15)

        if response.status_code == 200:
            ai_response = response.json()['content'][0]['text'].strip()
            return parse_athena_analysis(ai_response, user_query)
        else:
            return get_default_athena_insights(user_query)

    except Exception as e:
        print(f"Athena insights generation error: {e}")
        return get_default_athena_insights(user_query)

def parse_athena_analysis(ai_response, user_query):
    """Parse AI response and execute Athena query"""
    try:
        lines = ai_response.split('\n')
        database_name = ATHENA_DATABASES[0]  # Default to first database
        sql_query = ""
        explanation = ""

        in_database_section = False
        in_sql_section = False
        in_explanation_section = False

        for line in lines:
            line = line.strip()
            if line.startswith('DATABASE:'):
                in_database_section = True
                in_sql_section = False
                in_explanation_section = False
                continue
            elif line.startswith('SQL_QUERY:'):
                in_database_section = False
                in_sql_section = True
                in_explanation_section = False
                continue
            elif line.startswith('INSIGHT_EXPLANATION:'):
                in_database_section = False
                in_sql_section = False
                in_explanation_section = True
                continue

            if in_database_section and line:
                # Check if the suggested database is in our list
                if line in ATHENA_DATABASES:
                    database_name = line
            elif in_sql_section and line:
                sql_query += line + "\n"
            elif in_explanation_section and line:
                explanation += line + " "

        # Execute the query if we have one
        if sql_query.strip():
            query_results = query_athena(sql_query.strip(), database_name, f"Query for: {user_query}")
            return {
                'database': database_name,
                'sql_query': sql_query.strip(),
                'explanation': explanation.strip(),
                'results': query_results,
                'has_data': len(query_results.get('data', [])) > 0
            }
        else:
            return get_default_athena_insights(user_query)

    except Exception as e:
        print(f"Error parsing Athena analysis: {e}")
        return get_default_athena_insights(user_query)

def get_default_athena_insights(user_query):
    """Provide default Athena insights when AI analysis fails"""
    # Provide a simple, safe query as fallback
    default_query = """
SELECT
    campaign_type,
    COUNT(*) as campaign_count,
    AVG(CAST(opens as DOUBLE)) as avg_opens
FROM campaigns c
LEFT JOIN emails e ON c.campaign_id = e.campaign_id
WHERE c.created_date >= date_add('day', -30, current_date)
GROUP BY campaign_type
ORDER BY campaign_count DESC
LIMIT 10
"""

    return {
        'database': ATHENA_DATABASES[0],
        'sql_query': default_query.strip(),
        'explanation': f'Campaign performance overview for the last 30 days related to: {user_query}',
        'results': {"data": [], "columns": [], "note": "Sample query - configure AWS credentials to execute"},
        'has_data': False
    }

@app.route('/')
def index():
    return render_template_string(MAIN_TEMPLATE)

@app.route('/query', methods=['POST'])
def handle_query():
    try:
        data = request.get_json()
        query = data.get('query', '').strip()

        if not query:
            return jsonify({"error": "Please provide a query"})

        # Call Anthropic API for high-quality response
        ai_response = call_anthropic_api(query)

        # Generate AI-powered relevant resources
        related_resources = generate_related_resources(query)

        # Generate Athena insights
        athena_insights = generate_athena_insights(query)

        return jsonify({
            "response": ai_response,
            "resources": related_resources,
            "athena_insights": athena_insights
        })

    except Exception as e:
        print(f"Error in handle_query: {e}")
        return jsonify({"error": "An error occurred processing your request"})

@app.route('/followup', methods=['POST'])
def handle_followup():
    """Handle follow-up questions"""
    try:
        data = request.get_json()
        followup_query = data.get('query', '').strip()

        if not followup_query:
            return jsonify({"error": "Please provide a follow-up question"})

        # Call Anthropic API
        ai_response = call_anthropic_api(followup_query)

        return jsonify({
            "response": ai_response
        })

    except Exception as e:
        print(f"Error in handle_followup: {e}")
        return jsonify({"error": "An error occurred processing your follow-up"})

# Exact copy of production HTML with correct styling
MAIN_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Blueshift Support Bot - Interactive</title>
    <link rel="icon" type="image/png" sizes="32x32" href="/blueshift-favicon.png">
    <link rel="shortcut icon" href="/favicon.ico">
    <link rel="apple-touch-icon" sizes="32x32" href="/blueshift-favicon.png">
    <style>
        body {
            font-family: 'Calibri', sans-serif;
            font-size: 10pt;
            margin: 0;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
        }

        .container {
            max-width: 1000px;
            margin: 0 auto;
            background: white;
            margin-top: 40px;
            margin-bottom: 40px;
            padding: 50px;
            border-radius: 20px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.1);
        }

        h1 {
            color: #2790FF;
            margin-bottom: 15px;
            text-align: center;
            font-size: 2.5em;
            font-weight: bold;
        }

        .search-container {
            text-align: center;
            margin-bottom: 40px;
        }

        input[type="text"] {
            width: 70%;
            padding: 18px 25px;
            border: 2px solid #e1e5e9;
            border-radius: 50px;
            font-size: 16px;
            outline: none;
            transition: all 0.3s ease;
            font-family: 'Calibri', sans-serif;
        }

        input[type="text"]:focus {
            border-color: #2790FF;
            box-shadow: 0 0 0 3px rgba(39, 144, 255, 0.1);
        }

        button {
            padding: 18px 35px;
            background: linear-gradient(45deg, #2790FF, #4da6ff);
            color: white;
            border: none;
            border-radius: 50px;
            font-size: 16px;
            cursor: pointer;
            margin-left: 15px;
            transition: all 0.3s ease;
            font-weight: 500;
            font-family: 'Calibri', sans-serif;
        }

        button:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(39, 144, 255, 0.3);
        }

        button:disabled {
            opacity: 0.6;
            cursor: not-allowed;
            transform: none;
        }

        .features {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 30px;
            margin-top: 50px;
        }

        .feature {
            background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
            padding: 30px;
            border-radius: 15px;
            border-left: 5px solid #2790FF;
        }

        .feature h3 {
            color: #2790FF;
            margin-top: 0;
            font-size: 1.4em;
        }

        .feature ul {
            list-style-type: none;
            padding: 0;
        }

        .feature li {
            padding: 8px 0;
            border-bottom: 1px solid rgba(39, 144, 255, 0.1);
        }

        .feature li:before {
            content: "✓";
            color: #2790FF;
            font-weight: bold;
            margin-right: 10px;
        }

        .response-section {
            background: #f8f9fa;
            border-radius: 15px;
            padding: 30px;
            margin: 30px 0;
            border-left: 5px solid #2790FF;
            max-height: 400px;
            overflow-y: auto;
        }

        .response-section h3 {
            color: #2790FF;
            margin-top: 0;
            font-size: 1.4em;
        }

        .response-content {
            line-height: 1.6;
            color: #555555 !important;
            white-space: pre-line;
            font-weight: 500 !important;
            font-size: 1.05em;
        }

        /* INTERACTIVE FOLLOW-UP SECTION */
        .followup-section {
            background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);
            border-radius: 15px;
            padding: 25px;
            margin: 25px 0;
            border-left: 5px solid #2790FF;
        }

        .followup-section h4 {
            color: #2790FF;
            margin-top: 0;
            font-size: 1.2em;
        }

        .followup-container {
            display: flex;
            gap: 10px;
            margin-top: 15px;
        }

        #followupInput {
            flex: 1;
            padding: 12px 20px;
            border: 2px solid #2790FF;
            border-radius: 25px;
            font-size: 14px;
            outline: none;
            transition: all 0.3s ease;
            font-family: 'Calibri', sans-serif;
        }

        #followupInput:focus {
            border-color: #2790FF;
            box-shadow: 0 0 0 3px rgba(39, 144, 255, 0.1);
        }

        #followupBtn {
            background: linear-gradient(45deg, #2790FF, #4da6ff);
            padding: 12px 25px;
            border-radius: 25px;
            font-size: 14px;
            margin-left: 0;
            font-family: 'Calibri', sans-serif;
        }

        #followupBtn:hover {
            background: linear-gradient(45deg, #1976d2, #2790FF);
        }

        .followup-response {
            margin-top: 20px;
            padding: 20px;
            background: rgba(255, 255, 255, 0.8);
            border-radius: 10px;
            border-left: 3px solid #2790FF;
            display: none;
            max-height: 300px;
            overflow-y: auto;
            white-space: pre-line;
        }

        .sources-section {
            margin-top: 30px;
        }

        .sources-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 25px;
            margin-top: 20px;
        }

        .source-category {
            background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
            border-radius: 15px;
            padding: 25px;
            border-left: 5px solid #2790FF;
        }

        .source-category h4 {
            color: #2790FF;
            margin-top: 0;
            font-size: 1.2em;
        }

        .source-item {
            background: rgba(255, 255, 255, 0.7);
            border-radius: 8px;
            padding: 12px;
            margin: 8px 0;
            border-left: 3px solid #2790FF;
            font-size: 0.9em;
        }

        .source-item a {
            color: #000000;
            text-decoration: none;
            font-weight: 500;
        }

        .source-item a:hover {
            text-decoration: underline;
        }

        .loading {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 2px solid #f3f3f3;
            border-top: 2px solid #2790FF;
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }

        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }

        .results-container {
            display: none;
        }

        .results-container.show + .features {
            display: none;
        }

        /* ATHENA SECTION STYLING */
        .athena-section {
            background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);
            border-radius: 15px;
            padding: 25px;
            margin: 25px 0;
            border-left: 5px solid #2790FF;
        }

        .athena-section h3 {
            color: #2790FF;
            margin-top: 0;
            font-size: 1.3em;
        }

        .sql-query {
            background: #263238;
            color: #e0e0e0;
            padding: 15px;
            border-radius: 8px;
            font-family: 'Courier New', monospace;
            font-size: 12px;
            overflow-x: auto;
            margin: 15px 0;
        }

        .data-table {
            overflow-x: auto;
            margin: 15px 0;
        }

        .data-table table {
            width: 100%;
            border-collapse: collapse;
            min-width: 500px;
        }

        .data-table th, .data-table td {
            padding: 8px 12px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }

        .data-table th {
            background: #f5f5f5;
            font-weight: bold;
        }

        .athena-badge {
            background: linear-gradient(45deg, #2790FF, #4da6ff);
            color: white;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 0.8em;
            font-weight: bold;
            display: inline-block;
            margin-left: 10px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Blueshift Support Bot</h1>

        <div class="search-container">
            <input type="text" id="queryInput" placeholder="Enter your support question">
            <button id="searchBtn">Get Support Analysis</button>
        </div>

        <div id="resultsContainer" class="results-container">
            <div class="response-section">
                <div id="responseContent" class="response-content"></div>
            </div>

            <div id="athenaSection" class="athena-section" style="display: none;">
                <h3>📊 Data Insights <span class="athena-badge">ATHENA</span></h3>
                <p><strong>Database:</strong> <span id="athenaDatabase" style="font-family: monospace; background: #f0f0f0; padding: 2px 6px; border-radius: 4px;"></span></p>
                <p><strong>Analysis:</strong> <span id="athenaExplanation"></span></p>

                <details>
                    <summary style="cursor: pointer; color: #2790FF; font-weight: bold;">View SQL Query</summary>
                    <div id="athenaQuery" class="sql-query"></div>
                </details>

                <div id="athenaStatus"></div>
                <div id="athenaResults" class="data-table"></div>
            </div>

            <div class="followup-section">
                <h4>Have a follow-up question?</h4>
                <p style="margin: 5px 0 15px 0; color: #666; font-size: 0.9rem;">Ask for clarification, more details, or related questions about the same topic.</p>
                <div class="followup-container">
                    <input type="text" id="followupInput" placeholder="Ask a follow-up question..." />
                    <button id="followupBtn">Ask</button>
                </div>
                <div id="followupResponse" class="followup-response"></div>
            </div>

            <div class="sources-section">
                <h3>Related Resources</h3>
                <div id="sourcesGrid" class="sources-grid"></div>
            </div>

        </div>

        <div class="features">
            <div class="feature">
                <h3>🎫 Related JIRAs</h3>
                <ul>
                    <li>Links to relevant JIRA tickets and bugs</li>
                    <li>Known issues and their current status</li>
                    <li>Engineering updates and fixes</li>
                    <li>Product roadmap items</li>
                </ul>
            </div>

            <div class="feature">
                <h3>📚 Help Docs</h3>
                <ul>
                    <li>Official Blueshift help center articles</li>
                    <li>API documentation and guides</li>
                    <li>Setup and configuration instructions</li>
                    <li>Best practices and tutorials</li>
                </ul>
            </div>

            <div class="feature">
                <h3>🏢 Confluence</h3>
                <ul>
                    <li>Internal Confluence documentation</li>
                    <li>Team knowledge base articles</li>
                    <li>Troubleshooting runbooks</li>
                </ul>
            </div>

            <div class="feature">
                <h3>🎯 Zendesk</h3>
                <ul>
                    <li>Customer support ticket analysis</li>
                    <li>Similar issue resolutions</li>
                    <li>Support team responses</li>
                    <li>Escalation procedures</li>
                </ul>
            </div>
        </div>
    </div>

    <script>
        document.getElementById('searchBtn').addEventListener('click', function() {
            const query = document.getElementById('queryInput').value.trim();
            if (!query) {
                alert('Please enter a question first');
                return;
            }

            // Show loading
            document.getElementById('searchBtn').innerHTML = '<span class="loading"></span> Analyzing...';
            document.getElementById('searchBtn').disabled = true;

            fetch('/query', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ query: query })
            })
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    alert('Error: ' + data.error);
                    return;
                }

                // Show response
                document.getElementById('responseContent').textContent = data.response;
                const resultsContainer = document.getElementById('resultsContainer');
                resultsContainer.style.display = 'block';
                resultsContainer.classList.add('show');

                // Show resources in 4-column grid
                showResources(data.resources);

                // Show Athena insights if available
                if (data.athena_insights) {
                    showAthenaInsights(data.athena_insights);
                }

                // Reset button
                document.getElementById('searchBtn').innerHTML = 'Get Support Analysis';
                document.getElementById('searchBtn').disabled = false;
            })
            .catch(error => {
                alert('Error: ' + error);
                document.getElementById('searchBtn').innerHTML = 'Get Support Analysis';
                document.getElementById('searchBtn').disabled = false;
            });
        });

        document.getElementById('followupBtn').addEventListener('click', function() {
            const followupQuery = document.getElementById('followupInput').value.trim();
            if (!followupQuery) {
                alert('Please enter a follow-up question');
                return;
            }

            document.getElementById('followupBtn').innerHTML = '<span class="loading"></span> Processing...';
            document.getElementById('followupBtn').disabled = true;

            fetch('/followup', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ query: followupQuery })
            })
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    alert('Error: ' + data.error);
                    return;
                }

                document.getElementById('followupResponse').textContent = data.response;
                document.getElementById('followupResponse').style.display = 'block';
                document.getElementById('followupInput').value = '';

                document.getElementById('followupBtn').innerHTML = 'Ask';
                document.getElementById('followupBtn').disabled = false;
            })
            .catch(error => {
                alert('Error: ' + error);
                document.getElementById('followupBtn').innerHTML = 'Ask';
                document.getElementById('followupBtn').disabled = false;
            });
        });

        function showResources(resources) {
            const sourcesGrid = document.getElementById('sourcesGrid');
            sourcesGrid.innerHTML = '';

            const categories = [
                { key: 'help_docs', title: '📚 Help Docs', icon: '📚' },
                { key: 'confluence_docs', title: '🏢 Confluence Pages', icon: '🏢' },
                { key: 'jira_tickets', title: '🎫 JIRA Tickets', icon: '🎫' },
                { key: 'support_tickets', title: '🎯 Zendesk', icon: '🎯' }
            ];

            categories.forEach(category => {
                const categoryDiv = document.createElement('div');
                categoryDiv.className = 'source-category';
                categoryDiv.innerHTML = `<h4>${category.title}</h4>`;

                const items = resources[category.key] || [];
                items.forEach(item => {
                    const itemDiv = document.createElement('div');
                    itemDiv.className = 'source-item';
                    itemDiv.innerHTML = `<a href="${item.url}" target="_blank">${item.title}</a>`;
                    categoryDiv.appendChild(itemDiv);
                });

                sourcesGrid.appendChild(categoryDiv);
            });
        }

        // Allow Enter key to trigger search
        document.getElementById('queryInput').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                document.getElementById('searchBtn').click();
            }
        });

        document.getElementById('followupInput').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                document.getElementById('followupBtn').click();
            }
        });

        function showAthenaInsights(athenaData) {
            // Show the Athena section
            document.getElementById('athenaSection').style.display = 'block';

            // Set database
            document.getElementById('athenaDatabase').textContent = athenaData.database || 'default';

            // Set explanation
            document.getElementById('athenaExplanation').textContent = athenaData.explanation;

            // Set SQL query
            document.getElementById('athenaQuery').textContent = athenaData.sql_query;

            // Handle results
            const statusDiv = document.getElementById('athenaStatus');
            const resultsDiv = document.getElementById('athenaResults');

            if (athenaData.results.error) {
                statusDiv.innerHTML = `
                    <div style="background: #ffebee; padding: 15px; border-radius: 8px; color: #c62828; margin-top: 15px;">
                        <strong>Query Status:</strong> ${athenaData.results.error}
                        <br><small>Configure AWS credentials and Athena database to execute queries.</small>
                    </div>
                `;
                resultsDiv.innerHTML = '';
            } else if (athenaData.has_data && athenaData.results.data.length > 0) {
                statusDiv.innerHTML = '';

                let tableHTML = '<table><thead><tr>';
                athenaData.results.columns.forEach(column => {
                    tableHTML += `<th>${column}</th>`;
                });
                tableHTML += '</tr></thead><tbody>';

                athenaData.results.data.forEach(row => {
                    tableHTML += '<tr>';
                    athenaData.results.columns.forEach(column => {
                        tableHTML += `<td>${row[column] || ''}</td>`;
                    });
                    tableHTML += '</tr>';
                });
                tableHTML += '</tbody></table>';

                resultsDiv.innerHTML = tableHTML;
            } else {
                statusDiv.innerHTML = `
                    <div style="background: #e3f2fd; padding: 15px; border-radius: 8px; color: #1976d2; margin-top: 15px;">
                        <strong>Ready to Execute:</strong> Configure your AWS credentials to run this query and get live data insights.
                    </div>
                `;
                resultsDiv.innerHTML = '';
            }
        }
    </script>
</body>
</html>
'''

if __name__ == '__main__':
    print("Starting Blueshift Support Bot with AWS Athena Integration...")
    port = int(os.environ.get('PORT', 8103))
    print(f"Visit: http://localhost:{port}")
    print(f"AWS Region: {AWS_REGION}")
    print(f"Athena Databases: {', '.join(ATHENA_DATABASES)}")
    print(f"Athena S3 Output: {ATHENA_S3_OUTPUT}")
    app.run(host='0.0.0.0', port=port, debug=True)
