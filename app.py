from flask import Flask, request, jsonify, render_template_string
import requests
import os
import boto3
import json
from datetime import datetime
import time
import base64
import logging

# Try to load .env file if it exists (for development/testing)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed, continue without it

app = Flask(__name__)

# Use the correct Claude API key
AI_API_KEY = os.environ.get('CLAUDE_API_KEY')

# AWS Athena configuration - set these via environment variables
ATHENA_DATABASES = os.environ.get('ATHENA_DATABASE', 'customer_campaign_logs').split(',')
ATHENA_S3_OUTPUT = os.environ.get('ATHENA_S3_OUTPUT', 's3://bsft-customers/')
AWS_REGION = os.environ.get('AWS_REGION', 'us-west-2')

# API Configuration for searches
JIRA_URL = os.environ.get('JIRA_URL', 'https://blueshift.atlassian.net')
JIRA_TOKEN = os.environ.get('JIRA_TOKEN')
JIRA_EMAIL = os.environ.get('JIRA_EMAIL')

CONFLUENCE_URL = os.environ.get('CONFLUENCE_URL', 'https://blueshift.atlassian.net/wiki')
CONFLUENCE_TOKEN = os.environ.get('CONFLUENCE_TOKEN')
CONFLUENCE_EMAIL = os.environ.get('CONFLUENCE_EMAIL')

ZENDESK_SUBDOMAIN = os.environ.get('ZENDESK_SUBDOMAIN')
ZENDESK_TOKEN = os.environ.get('ZENDESK_TOKEN')
ZENDESK_EMAIL = os.environ.get('ZENDESK_EMAIL')

# Configure logging for production debugging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Debug environment variables on startup
logger.info(f"Environment variables loaded:")
logger.info(f"JIRA_TOKEN: {'SET' if JIRA_TOKEN else 'NOT SET'}")
logger.info(f"JIRA_EMAIL: {'SET' if JIRA_EMAIL else 'NOT SET'}")
logger.info(f"CONFLUENCE_TOKEN: {'SET' if CONFLUENCE_TOKEN else 'NOT SET'}")
logger.info(f"CONFLUENCE_EMAIL: {'SET' if CONFLUENCE_EMAIL else 'NOT SET'}")
logger.info(f"ZENDESK_TOKEN: {'SET' if ZENDESK_TOKEN else 'NOT SET'}")
logger.info(f"ZENDESK_SUBDOMAIN: {'SET' if ZENDESK_SUBDOMAIN else 'NOT SET'}")

def call_anthropic_api(query):
    """Call Anthropic Claude API for high-quality responses"""
    try:
        headers = {
            'x-api-key': AI_API_KEY,
            'Content-Type': 'application/json',
            'anthropic-version': '2023-06-01'
        }

        prompt = f"""You are a Blueshift expert. Provide comprehensive, detailed answers for support queries.

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

def search_jira_tickets(query, limit=3):
    """Search JIRA tickets using API with improved error handling"""
    try:
        if not JIRA_TOKEN or not JIRA_EMAIL:
            logger.warning("JIRA credentials not configured - using fallback")
            return []

        auth = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_TOKEN}".encode()).decode()
        headers = {
            'Authorization': f'Basic {auth}',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }

        # Use the migrated JQL endpoint as recommended by Atlassian
        jql = f'text ~ "{query}" ORDER BY updated DESC'

        # Try the new jql endpoint first, fallback if it doesn't work
        url = f"{JIRA_URL}/rest/api/3/search/jql"

        payload = {
            'jql': jql,
            'maxResults': limit,
            'fields': ['summary', 'key', 'status']
        }

        response = requests.post(url, headers=headers, json=payload, timeout=15)

        if response.status_code == 200:
            data = response.json()
            results = []
            for issue in data.get('issues', []):
                results.append({
                    'title': f"{issue['key']}: {issue['fields']['summary']}",
                    'url': f"{JIRA_URL}/browse/{issue['key']}"
                })
            logger.info(f"JIRA search found {len(results)} results")
            return results
        else:
            logger.error(f"JIRA API error: {response.status_code} - {response.text[:200]}")
    except Exception as e:
        logger.error(f"JIRA search error: {e}")

    return []

def search_confluence_docs(query, limit=3):
    """Search Confluence pages using API with improved error handling"""
    try:
        if not CONFLUENCE_TOKEN or not CONFLUENCE_EMAIL:
            logger.warning("Confluence credentials not configured - using fallback")
            return []

        auth = base64.b64encode(f"{CONFLUENCE_EMAIL}:{CONFLUENCE_TOKEN}".encode()).decode()
        headers = {
            'Authorization': f'Basic {auth}',
            'Accept': 'application/json'
        }

        # Use search API for better relevance scoring
        url = f"{CONFLUENCE_URL}/rest/api/search"

        # Use comprehensive search - let the API find ALL relevant pages
        # Search both title and text for the full query AND individual keywords
        query_clean = query.strip()

        # Build a comprehensive CQL query that searches broadly
        search_conditions = []

        # Search for the exact phrase first
        search_conditions.append(f'title ~ "{query_clean}"')
        search_conditions.append(f'text ~ "{query_clean}"')

        # Also search for individual important keywords
        keywords = query_clean.lower().split()
        for keyword in keywords:
            if len(keyword.strip()) > 2:  # Skip very short words
                search_conditions.append(f'title ~ "{keyword.strip()}"')
                search_conditions.append(f'text ~ "{keyword.strip()}"')

        # Use OR to get maximum coverage - let relevance scoring handle ranking
        cql_query = f'type = "page" AND ({" OR ".join(search_conditions)})'

        logger.info(f"Confluence CQL query: {cql_query}")

        response = requests.get(url, headers=headers, params={
            'cql': cql_query,
            'limit': 20,  # Get many more results to find truly relevant ones
            'expand': 'space'
        }, timeout=15)

        if response.status_code == 200:
            data = response.json()
            scored_results = []

            for result in data.get('results', []):
                title = result.get('title', 'Untitled')
                space_key = result.get('space', {}).get('key', '')
                page_id = result.get('id', '')

                # Trust Confluence API's relevance scoring - it knows content, not just titles
                api_score = result.get('score', 0)

                # Use API score as primary relevance (it considers full content, not just titles)
                relevance_score = api_score if api_score > 0 else 0.1

                # Add small bonuses for title matches, but don't require them
                title_lower = title.lower()
                query_lower = query.lower()

                # Bonus for exact phrase match in title
                if query_lower in title_lower:
                    relevance_score += 10

                # Bonus for individual words in title
                query_words_in_query = query_lower.split()
                words_in_title = sum(1 for word in query_words_in_query if word.strip() in title_lower)
                relevance_score += words_in_title * 2

                # Don't filter out any results - let API decide what's relevant

                # Debug logging to see what we're getting
                logger.info(f"Confluence result: title='{title}', space='{space_key}', score={relevance_score}")

                # Debug: log all available fields to understand the response structure
                logger.info(f"Confluence result fields: {list(result.keys())}")
                if '_links' in result:
                    logger.info(f"Available links: {result['_links'].keys()}")

                # Try to get the direct URL from the API response first
                full_url = result.get('url', '')

                if full_url:
                    logger.info(f"Using direct API URL: {full_url}")
                else:
                    # Fallback to URL construction
                    web_link = result.get('_links', {}).get('webui', '')
                    if web_link:
                        full_url = f"{CONFLUENCE_URL.replace('/wiki', '').rstrip('/')}{web_link}"
                        logger.info(f"Using webui link: {full_url}")
                    elif space_key and page_id:
                        full_url = f"{CONFLUENCE_URL.replace('/wiki', '').rstrip('/')}/wiki/spaces/{space_key}/pages/{page_id}"
                        logger.info(f"Using constructed URL: {full_url}")
                    else:
                        # Create a search URL as last resort
                        encoded_title = title.replace(' ', '+').replace('?', '')
                        full_url = f"{CONFLUENCE_URL}/dosearchsite.action?queryString={encoded_title}"
                        logger.info(f"Using search fallback: {full_url}")

                if not full_url:
                    logger.warning(f"Could not construct URL for Confluence result '{title}'")
                    continue

                scored_results.append({
                    'title': title,
                    'url': full_url,
                    'score': relevance_score
                })

            # Sort by relevance score and return top results
            scored_results.sort(key=lambda x: x['score'], reverse=True)
            results = [{'title': r['title'], 'url': r['url']} for r in scored_results[:limit]]

            logger.info(f"Confluence search found {len(results)} valid results")
            return results
        else:
            logger.error(f"Confluence API error: {response.status_code} - {response.text[:200]}")
    except Exception as e:
        logger.error(f"Confluence search error: {e}")

    return []

def search_zendesk_tickets(query, limit=3):
    """Search Zendesk tickets using API with improved error handling"""
    try:
        if not ZENDESK_TOKEN or not ZENDESK_SUBDOMAIN:
            logger.warning("Zendesk credentials not configured - using fallback")
            return []

        # Try Basic Auth with email/token combination first
        if ZENDESK_EMAIL:
            auth = base64.b64encode(f"{ZENDESK_EMAIL}/token:{ZENDESK_TOKEN}".encode()).decode()
            headers = {
                'Authorization': f'Basic {auth}',
                'Accept': 'application/json'
            }
        else:
            # Fallback to Bearer token
            headers = {
                'Authorization': f'Bearer {ZENDESK_TOKEN}',
                'Accept': 'application/json'
            }

        # Search API endpoint
        url = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json"

        response = requests.get(url, headers=headers, params={
            'query': f'{query} type:ticket',
            'per_page': limit
        }, timeout=15)

        if response.status_code == 200:
            data = response.json()
            results = []
            for ticket in data.get('results', [])[:limit]:
                results.append({
                    'title': f"Ticket #{ticket['id']}: {ticket.get('subject', 'No Subject')}",
                    'url': f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/agent/tickets/{ticket['id']}"
                })
            logger.info(f"Zendesk search found {len(results)} results")
            return results
        else:
            logger.error(f"Zendesk API error: {response.status_code} - {response.text[:200]}")
    except Exception as e:
        logger.error(f"Zendesk search error: {e}")

    return []

def search_help_docs(query, limit=3):
    """Search Blueshift Help Center using Zendesk Help Center API"""
    try:
        # Use Zendesk Help Center API to search articles
        if not ZENDESK_SUBDOMAIN or not ZENDESK_TOKEN:
            logger.warning("Zendesk Help Center credentials not configured - using fallback")
        else:
            # Set up authentication
            if ZENDESK_EMAIL:
                auth = base64.b64encode(f"{ZENDESK_EMAIL}/token:{ZENDESK_TOKEN}".encode()).decode()
                headers = {
                    'Authorization': f'Basic {auth}',
                    'Accept': 'application/json'
                }
            else:
                headers = {
                    'Authorization': f'Bearer {ZENDESK_TOKEN}',
                    'Accept': 'application/json'
                }

            # Use the Help Center articles search API
            search_url = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/help_center/articles/search.json"
            response = requests.get(search_url, headers=headers, params={
                'query': query,
                'per_page': 10  # Get more results to find all relevant articles
            }, timeout=15)

            if response.status_code == 200:
                data = response.json()
                results = []
                for article in data.get('results', []):
                    title = article.get('title', 'Untitled')
                    url = article.get('html_url', '')

                    if title and url:
                        results.append({
                            'title': title,
                            'url': url
                        })

                if results:
                    logger.info(f"Help Center API search found {len(results)} results for '{query}'")
                    for i, doc in enumerate(results):
                        logger.info(f"  {i+1}. {doc['title']}")
                    return results
                else:
                    logger.info(f"Help Center API search returned no results for '{query}'")
            else:
                logger.error(f"Help Center API error: {response.status_code} - {response.text[:200]}")

    except Exception as e:
        logger.error(f"Help Center API search error: {e}")

    # Enhanced curated list fallback with better matching for external fetch issues
    help_docs_expanded = [
        {"title": "API Integration Guide", "url": "https://help.blueshift.com/hc/en-us/articles/115002714053", "keywords": ["api", "integration", "developer", "external", "fetch", "webhook", "endpoint"]},
        {"title": "Common API Implementation Issues", "url": "https://help.blueshift.com/hc/en-us/articles/115002713773", "keywords": ["issues", "problems", "troubleshoot", "failing", "error", "api", "external", "fetch", "timeout", "connection"]},
        {"title": "Event Tracking API Documentation", "url": "https://help.blueshift.com/hc/en-us/articles/115002713453", "keywords": ["event", "tracking", "data", "api", "external", "fetch", "post", "send"]},
        {"title": "Custom API Endpoints", "url": "https://help.blueshift.com/hc/en-us/articles/115002714173", "keywords": ["custom", "api", "endpoint", "external", "integration", "fetch", "data"]},
        {"title": "External Data Integration", "url": "https://help.blueshift.com/hc/en-us/articles/115002714253", "keywords": ["external", "data", "integration", "fetch", "import", "sync", "api"]},
        {"title": "Webhook Configuration", "url": "https://help.blueshift.com/hc/en-us/articles/115002714333", "keywords": ["webhook", "external", "fetch", "callback", "api", "endpoint", "configuration"]},
        {"title": "Data Import Troubleshooting", "url": "https://help.blueshift.com/hc/en-us/articles/115002714413", "keywords": ["data", "import", "troubleshoot", "external", "fetch", "sync", "error", "failing"]},
        {"title": "Authentication and API Keys", "url": "https://help.blueshift.com/hc/en-us/articles/115002714493", "keywords": ["authentication", "api", "key", "token", "external", "access", "security"]},
        {"title": "Error Handling Best Practices", "url": "https://help.blueshift.com/hc/en-us/articles/115002714653", "keywords": ["error", "handling", "best", "practices", "api", "external", "fetch", "retry", "timeout"]},
        {"title": "Real-time Data Processing", "url": "https://help.blueshift.com/hc/en-us/articles/115002714573", "keywords": ["realtime", "data", "processing", "external", "fetch", "stream", "api"]}
    ]

    query_lower = query.lower()
    query_words = set(query_lower.split())

    # Enhanced scoring system
    scored_docs = []
    for doc in help_docs_expanded:
        score = 0

        # Title matching (highest weight)
        title_words = set(doc['title'].lower().split())
        title_matches = query_words.intersection(title_words)
        score += len(title_matches) * 5

        # Keyword matching
        keyword_words = set(' '.join(doc['keywords']).lower().split())
        keyword_matches = query_words.intersection(keyword_words)
        score += len(keyword_matches) * 3

        # Phrase matching bonus
        for query_word in query_words:
            if query_word in doc['title'].lower():
                score += 2
            if query_word in ' '.join(doc['keywords']).lower():
                score += 1

        # Special handling for common technical terms
        if 'external' in query_lower and 'fetch' in query_lower:
            if 'external' in doc['keywords'] and 'fetch' in doc['keywords']:
                score += 5

        if score > 0:
            scored_docs.append((score, doc))

    # Sort by score and return top results
    scored_docs.sort(reverse=True, key=lambda x: x[0])
    results = [doc for score, doc in scored_docs[:limit]]

    logger.info(f"Help docs curated search: '{query}' -> found {len(results)} results")
    for i, doc in enumerate(results):
        logger.info(f"  {i+1}. {doc['title']}")

    return results

def generate_related_resources(query):
    """Generate contextually relevant resources using API searches with smart fallbacks"""
    logger.info(f"Searching for resources: {query}")

    # Perform API searches with proper error handling
    help_docs = search_help_docs(query, limit=3)
    confluence_docs = search_confluence_docs(query, limit=3)
    jira_tickets = search_jira_tickets(query, limit=3)
    support_tickets = search_zendesk_tickets(query, limit=3)

    # No fallbacks - return only actual API results

    logger.info(f"Resource counts: help={len(help_docs)}, confluence={len(confluence_docs)}, jira={len(jira_tickets)}, zendesk={len(support_tickets)}")

    return {
        'help_docs': help_docs,
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
            status_details = result['QueryExecution']['Status']
            error_msg = status_details.get('StateChangeReason', 'Query failed')
            failure_reason = status_details.get('AthenaError', {}).get('ErrorMessage', 'No additional error details')

            print(f"Athena query failed:")
            print(f"  Status: {status}")
            print(f"  StateChangeReason: {error_msg}")
            print(f"  AthenaError: {failure_reason}")
            print(f"  Full status: {status_details}")

            return {"error": f"Query failed: {error_msg}. Details: {failure_reason}", "data": []}

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
        print(f"Query was: {query_string}")
        return {"error": str(e), "data": []}

def customize_query_for_execution(sql_query, user_query):
    """Customize the generated query with more realistic parameters for execution"""

    # Use your real account UUID as default
    real_account_uuid = '11d490bf-b250-4749-abf4-b6197620a985'

    # Replace generic UUIDs with real ones
    customized = sql_query.replace('uuid-value', real_account_uuid)
    customized = customized.replace('account_uuid = \'uuid-value\'', f'account_uuid = \'{real_account_uuid}\'')

    # Use more recent dates that are likely to have data
    import datetime
    recent_date = (datetime.datetime.now() - datetime.timedelta(days=30)).strftime('%Y-%m-%d')

    # Replace overly restrictive date ranges
    customized = customized.replace('file_date >= \'2025-01-01\'', f'file_date >= \'{recent_date}\'')
    customized = customized.replace('file_date >= \'2024-08-01\'', f'file_date >= \'{recent_date}\'')

    # For ExternalFetchError example, use recent date
    if 'ExternalFetchError' in user_query.lower() or 'fetch' in user_query.lower():
        customized = customized.replace('AND file_date >= ', f'AND file_date = \'{recent_date}\' AND file_date >= ')
        customized = customized.replace(f'AND file_date = \'{recent_date}\' AND file_date >= \'{recent_date}\'', f'AND file_date >= \'{recent_date}\'')

    return customized

def get_available_tables(database_name):
    """Get list of available tables in the database"""
    try:
        # Get a sample of tables to help AI understand the schema
        show_tables_query = f"SHOW TABLES IN {database_name}"
        result = query_athena(show_tables_query, database_name, "Get table list")

        if result.get('data'):
            # Return first 50 tables as a sample (to avoid overwhelming the AI)
            tables = [row[result['columns'][0]] for row in result['data'][:50]]
            return tables
        return []
    except Exception as e:
        print(f"Error getting tables: {e}")
        return []

def generate_athena_insights(user_query):
    """Generate data insights using Athena queries based on user query"""
    try:
        # Get available tables first
        database_name = ATHENA_DATABASES[0]  # Use first database
        available_tables = get_available_tables(database_name)
        table_list = ', '.join(available_tables[:20]) if available_tables else "campaign_execution_v3, and other tables"

        # Use AI to determine what kind of data query would be helpful
        headers = {
            'x-api-key': AI_API_KEY,
            'Content-Type': 'application/json',
            'anthropic-version': '2023-06-01'
        }

        database_list = ', '.join(ATHENA_DATABASES)
        analysis_prompt = f"""Analyze this Blueshift support query: "{user_query}"

Available tables in {database_name}: {table_list}

IMPORTANT: You must ONLY use the actual table names listed above. Do not use generic names.

Generate a SIMPLE troubleshooting SQL query for AWS Athena based on the user's question.

Common query patterns for different support issues:

For USER-SPECIFIC MESSAGING queries (why didn't user get messaged, user journey analysis):
SELECT timestamp, user_uuid, campaign_uuid, trigger_uuid, message, log_level
FROM customer_campaign_logs.campaign_execution_v3
WHERE account_uuid = 'ACCOUNT_UUID_HERE'
AND campaign_uuid = 'CAMPAIGN_UUID_HERE'
AND user_uuid = 'USER_UUID_HERE'
AND file_date >= '2024-12-01'
ORDER BY timestamp ASC

For ERROR/FAILURE analysis:
SELECT timestamp, user_uuid, campaign_uuid, trigger_uuid, message
FROM customer_campaign_logs.campaign_execution_v3
WHERE account_uuid = 'ACCOUNT_UUID_HERE'
AND campaign_uuid = 'CAMPAIGN_UUID_HERE'
AND log_level = 'ERROR'
AND file_date >= '2024-12-01'
ORDER BY timestamp ASC
LIMIT 100

For DEDUPLICATION issues:
SELECT user_uuid, campaign_uuid, message
FROM customer_campaign_logs.campaign_execution_v3
WHERE account_uuid = 'ACCOUNT_UUID_HERE'
AND (message LIKE '%dedup%' OR message LIKE '%duplicate%' OR message LIKE '%skipping%')
AND file_date >= '2024-12-01'
ORDER BY timestamp ASC

For CHANNEL LIMIT errors:
SELECT COUNT(DISTINCT(user_uuid))
FROM customer_campaign_logs.campaign_execution_v3
WHERE account_uuid = 'ACCOUNT_UUID_HERE'
AND campaign_uuid = 'CAMPAIGN_UUID_HERE'
AND message LIKE '%channel messaging limits hit%'
AND file_date >= '2024-12-01'

For SOFT BOUNCE analysis:
SELECT timestamp, user_uuid, campaign_uuid, message
FROM customer_campaign_logs.campaign_execution_v3
WHERE account_uuid = 'ACCOUNT_UUID_HERE'
AND campaign_uuid = 'CAMPAIGN_UUID_HERE'
AND message LIKE '%soft_bounce%'
AND file_date >= '2024-12-01'
ORDER BY timestamp DESC
LIMIT 10

For JSON extraction (email delivery details):
SELECT
    json_extract_scalar(message, '$.action') AS action,
    json_extract_scalar(message, '$.email') AS email,
    json_extract_scalar(message, '$.user_uuid') AS user_uuid,
    json_extract_scalar(message, '$.reason') AS reason
FROM customer_campaign_logs.campaign_execution_v3
WHERE account_uuid = 'ACCOUNT_UUID_HERE'
AND campaign_uuid = 'CAMPAIGN_UUID_HERE'
AND message LIKE '%soft_bounce%'
AND file_date >= '2024-12-01'

Key rules:
1. Use realistic placeholders: ACCOUNT_UUID_HERE, CAMPAIGN_UUID_HERE, USER_UUID_HERE
2. Use ONLY these columns: timestamp, user_uuid, campaign_uuid, trigger_uuid, message, log_level, file_date, execution_key, transaction_uuid, worker_name
3. Always include account_uuid filter (required for all queries)
4. For user-specific queries: Include user_uuid and campaign_uuid filters
5. For error analysis: Use log_level = 'ERROR' and specific message patterns
6. For counting queries: Use COUNT(DISTINCT(user_uuid)) pattern
7. Use recent dates: file_date >= '2024-12-01'
8. Common message patterns: '%dedup%', '%channel messaging limits hit%', '%soft_bounce%', '%ExternalFetchError%', '%NotEnoughRecommendationProductsError%'
9. Order by timestamp ASC for chronological analysis
10. Use JSON extraction for delivery details: json_extract_scalar(message, '$.field')
11. Include proper LIMIT clauses (10-100 for data queries)

If the available tables list is empty, create a simple SHOW TABLES query instead.

Provide:
1. The best database to use
2. A practical troubleshooting SQL query using ONLY the actual available table names
3. A brief explanation of what this query would help diagnose

Format:
DATABASE:
[chosen database name]

SQL_QUERY:
[your troubleshooting SQL query using only actual table names from the list above]

INSIGHT_EXPLANATION:
[brief explanation of what this query helps diagnose]"""

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
                # Clean up markdown formatting
                cleaned_line = line.replace('```sql', '').replace('```', '').strip()
                if cleaned_line:  # Only add non-empty lines
                    sql_query += cleaned_line + "\n"
            elif in_explanation_section and line:
                explanation += line + "\n"

        # Return the suggested query template for manual customization
        if sql_query.strip():
            print(f"Generated SQL Query: {sql_query.strip()}")  # Debug output
            return {
                'database': database_name,
                'sql_query': sql_query.strip(),
                'explanation': explanation.strip() + "\n\nCopy this query to Athena and customize with specific account_uuid, campaign_uuid, and date ranges for your support case.",
                'results': {"note": "Query template ready for manual customization in Athena", "data": []},
                'has_data': False
            }
        else:
            return get_default_athena_insights(user_query)

    except Exception as e:
        print(f"Error parsing Athena analysis: {e}")
        return get_default_athena_insights(user_query)

def get_default_athena_insights(user_query):
    """Provide default Athena insights when AI analysis fails"""
    # Use the simplest possible working query
    database_name = ATHENA_DATABASES[0]
    default_query = f"""SELECT timestamp, message
FROM {database_name}.campaign_execution_v3
WHERE log_level = 'ERROR'
ORDER BY timestamp DESC
LIMIT 10"""

    return {
        'database': database_name,
        'sql_query': default_query,
        'explanation': f'Recent error logs from campaign_execution_v3 related to: {user_query}',
        'results': {"data": [], "columns": [], "note": "Sample query - will show real data when executed"},
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
                <h3>📊 Suggested Query <span class="athena-badge">ATHENA</span></h3>
                <p><strong>Database:</strong> <span id="athenaDatabase" style="font-family: monospace; background: #f0f0f0; padding: 2px 6px; border-radius: 4px;"></span></p>
                <div><strong>Analysis:</strong></div>
                <div id="athenaExplanation" style="white-space: pre-line; margin-top: 8px; line-height: 1.6;"></div>

                <div style="margin: 15px 0;">
                    <label for="suggestedQuery" style="font-weight: bold; color: #2790FF;">Copy this query to Athena:</label>
                    <textarea id="suggestedQuery" class="sql-query" style="width: 100%; height: 120px; margin-top: 5px; font-family: 'Courier New', monospace; font-size: 12px; border: 2px solid #2790FF; border-radius: 8px; padding: 10px;" readonly placeholder="SQL query suggestion will appear here..."></textarea>
                    <p style="margin-top: 10px; color: #666; font-size: 0.9em;">💡 <strong>Instructions:</strong> Copy this query to AWS Athena console and customize with specific account_uuid, campaign_uuid, and date ranges for your support case.</p>
                </div>
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

            // Set editable SQL query
            document.getElementById('suggestedQuery').value = athenaData.sql_query;

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
