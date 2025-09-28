from flask import Flask, request, jsonify, render_template_string, send_file, session, redirect, url_for
import requests
import os
import boto3
import json
from datetime import datetime, timedelta
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
app.secret_key = 'blueshift_support_bot_secret_key_2023'
app.permanent_session_lifetime = timedelta(hours=12)

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

def call_anthropic_api(query, platform_resources=None):
    """Call Anthropic Claude API for high-quality responses with platform resource context"""
    try:
        headers = {
            'x-api-key': AI_API_KEY,
            'Content-Type': 'application/json',
            'anthropic-version': '2023-06-01'
        }

        # Add platform resources context if available - use content when available
        platform_context = ""
        if platform_resources and len(platform_resources) > 0:
            # Check if we have resources with actual content
            resources_with_content = [r for r in platform_resources if isinstance(r, dict) and 'content' in r]

            if resources_with_content:
                platform_context = "\n\nCOMPREHENSIVE PLATFORM DOCUMENTATION (FULL CONTENT FROM ALL SOURCES):\n"
                for i, resource in enumerate(resources_with_content[:4]):  # Top 4 with content from all sources
                    platform_context += f"{i+1}. [{resource.get('source', 'unknown').upper()}] {resource['title']}\n"
                    platform_context += f"   URL: {resource['url']}\n"
                    platform_context += f"   DETAILED CONTENT:\n{resource['content']}\n"
                    platform_context += "   " + "="*50 + "\n\n"
            else:
                # Fallback to just URLs
                platform_context = "\n\nRELEVANT PLATFORM DOCUMENTATION:\n"
                for i, resource in enumerate(platform_resources[:3]):
                    platform_context += f"{i+1}. {resource['title']}\n   URL: {resource['url']}\n"

        prompt = f"""You are a Blueshift Support agent helping troubleshoot client issues.

INTERNAL SUPPORT QUERY: {query}

CONTEXT: This is an internal tool used BY Blueshift support staff to troubleshoot customer tickets, not customer-facing.
{platform_context}

RESPONSE REQUIREMENTS:

STRUCTURE YOUR RESPONSE AS FOLLOWS:

## STEP-BY-STEP PLATFORM INSTRUCTIONS

MANDATORY REQUIREMENT: You have access to content from MULTIPLE sources - Help Docs, API Docs, Confluence, JIRA tickets, and Zendesk tickets. You MUST check ALL sources for platform navigation steps.

SEARCH ALL DOCUMENTATION SOURCES FOR:
1. **HELP DOCS**: Look for articles like "Campaign Studio", "Journey Tab", "Detail Mode", "Trigger Actions"
2. **CONFLUENCE**: Internal documentation with detailed platform workflows
3. **JIRA TICKETS**: Engineering tickets that mention platform navigation and UI elements
4. **ZENDESK TICKETS**: Support tickets where agents provided platform navigation steps to customers
5. **API DOCS**: Developer documentation with platform integration steps

CRITICAL INSTRUCTIONS:
1. READ through ALL documentation content from ALL sources provided above
2. Look for phrases like "Go to", "Click", "Navigate to", "Select", "Choose", "Access via"
3. Find UI elements like "Journey Tab", "Detail Mode", "Filter Conditions", "Campaign Studio"
4. Extract and format these as numbered step-by-step instructions
5. Use EXACT terminology from ANY of the documentation sources
6. Combine information from multiple sources if needed for complete steps

FORMAT YOUR RESPONSE EXACTLY LIKE THIS:
**Steps to check trigger filter conditions (if trigger not sending):**

1. Navigate to Campaign Studio → Journey Tab
2. Switch to "Detail Mode" to view full trigger configurations
3. Check filter criteria and delay parameters for the specific trigger
4. [Continue with all steps found across ALL documentation sources]

**What to check specifically:**
- [List specific things to verify from documentation]
- [Include exact field names and settings from docs]

**Prerequisites:** [Any requirements mentioned in documentation]
**Reference:** [List ALL sources that contained these steps - Help Docs, Confluence, JIRA, Zendesk]

EXAMPLE OF EXPECTED RESPONSE:
Based on the Campaign Studio help documentation and internal Confluence workflows:

**Steps to check trigger filter conditions:**
1. Navigate to Campaign Studio → Journey Tab
2. Switch to "Detail Mode" to see full trigger configurations
3. Click on the specific trigger that's not sending
4. Review filter criteria (shown in green)
5. Check delay parameters (shown in yellow)
6. Verify message settings (shown in blue)

**Reference:** Campaign Studio help doc, Confluence workflow guide, JIRA ticket BS-1234

IF documentation lacks specific platform steps, provide what's available and state which sources were checked.

## TROUBLESHOOTING GUIDANCE
Then provide technical troubleshooting information:

1. INTERNAL PERSPECTIVE: You are helping Blueshift support engineers, not customers. Never say "contact Blueshift support" - WE ARE the support team.

2. TECHNICAL TROUBLESHOOTING FOCUS:
   - Provide specific debugging steps
   - Suggest what to check in logs/databases
   - Recommend specific Athena queries to investigate
   - Point to relevant system components or error patterns

3. BLUESHIFT PLATFORM KNOWLEDGE:
   - API Base URL: https://api.getblueshift.com
   - Events API: POST https://api.getblueshift.com/api/v1/event
   - Customer API: POST https://api.getblueshift.com/api/v1/customers
   - Main troubleshooting database: customer_campaign_logs.campaign_execution_v3
   - Common error patterns: ExternalFetchError, ChannelLimitError, DeduplicationError

4. INTERNAL TROUBLESHOOTING RESPONSES:
   - "Check the campaign_execution_v3 logs for..."
   - "Look for specific error patterns in Athena..."
   - "This typically indicates..."
   - "To investigate further, query..."
   - "Common causes include..."

5. API EXAMPLES (when relevant):
     curl --request POST \\
          --url https://api.getblueshift.com/api/v1/customers \\
          --header 'accept: application/json' \\
          --header 'content-type: application/json' \\
          --data '{{
       "email": "user@example.com",
       "custom_attribute": "value"
     }}'

IMPORTANT: Always start with the step-by-step platform instructions, then follow with troubleshooting guidance. Be extremely specific about UI navigation and button names in the platform instructions section.

NEVER suggest contacting Blueshift support - provide direct troubleshooting guidance for internal support engineers."""

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

def search_jira_tickets(query, limit=5, debug=True):
    """Search JIRA tickets with improved relevance and stop word filtering"""
    try:
        if not JIRA_TOKEN or not JIRA_EMAIL:
            logger.warning("JIRA credentials not configured - using fallback")
            return []

        # Same stop words as Confluence
        STOP_WORDS = {'why', 'is', 'my', 'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'how', 'what', 'when', 'where', 'who'}

        def clean_words(words):
            """Remove stop words and short words"""
            return [w for w in words if len(w) > 2 and w.lower() not in STOP_WORDS]

        auth = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_TOKEN}".encode()).decode()
        headers = {
            'Authorization': f'Basic {auth}',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }

        # --- Clean query words ---
        words = query.strip().split()
        clean_query_words = clean_words(words)

        # If we filtered out everything, use original words
        if not clean_query_words:
            clean_query_words = words

        logger.info(f"JIRA search - Original: '{query}' -> Clean words: {clean_query_words}")

        # --- Build JQL queries progressively ---
        jql_variants = []

        # 1. Exact phrase in summary (titles are most relevant)
        jql_variants.append(f'summary ~ "\\"{query}\\"" ORDER BY updated DESC')

        # 2. Clean words AND in summary and text
        if len(clean_query_words) > 1:
            and_parts = [f'(summary ~ "{w}" OR text ~ "{w}")' for w in clean_query_words]
            jql_variants.append(f'({" AND ".join(and_parts)}) ORDER BY updated DESC')

        # 3. Clean words OR in summary and text
        or_parts = [f'(summary ~ "{w}" OR text ~ "{w}")' for w in clean_query_words]
        jql_variants.append(f'({" OR ".join(or_parts)}) ORDER BY updated DESC')

        # 4. Single most important word in summary only
        if len(clean_query_words) > 1:
            main_word = max(clean_query_words, key=len)
            jql_variants.append(f'summary ~ "{main_word}" ORDER BY updated DESC')

        url = f"{JIRA_URL}/rest/api/3/search/jql"

        # --- Try queries in order ---
        final_issues = []
        for i, jql in enumerate(jql_variants):
            try:
                logger.info(f"Trying JIRA JQL #{i+1}: {jql}")

                payload = {
                    'jql': jql,
                    'maxResults': limit * 3,  # Get more for filtering
                    'fields': ['summary', 'key', 'status', 'priority', 'issuetype']
                }

                response = requests.post(url, headers=headers, json=payload, timeout=15)

                if response.status_code == 200:
                    data = response.json()
                    issues = data.get('issues', [])

                    if issues:
                        logger.info(f"JIRA query #{i+1} returned {len(issues)} results")
                        final_issues = issues
                        break
                    else:
                        logger.info(f"JIRA query #{i+1} returned no results")
                else:
                    logger.error(f"JIRA API error on query #{i+1}: {response.status_code}")

            except Exception as e:
                logger.error(f"JIRA query #{i+1} failed: {e}")
                continue

        if not final_issues:
            logger.info("No JIRA results found with any query variant")
            return []

        # --- Debug: log raw results ---
        if debug and final_issues:
            logger.info("---- Raw JIRA Results ----")
            for issue in final_issues[:10]:
                key = issue.get('key', 'N/A')
                summary = issue.get('fields', {}).get('summary', 'No summary')
                status = issue.get('fields', {}).get('status', {}).get('name', 'Unknown')
                logger.info(f"Key: {key} | Status: {status} | Summary: {summary}")
            logger.info("---- End JIRA Results ----")

        # --- Score and filter results ---
        def score_issue(issue):
            summary = issue.get('fields', {}).get('summary', '').lower()

            # Count clean word matches in summary
            matches = sum(1 for word in clean_query_words if word.lower() in summary)

            # Bonus for exact query in summary
            exact_bonus = 10 if query.lower() in summary else 0

            # Priority bonus (higher priority = more relevant)
            priority = issue.get('fields', {}).get('priority', {})
            priority_name = priority.get('name', '').lower() if priority else ''
            priority_bonus = 5 if 'high' in priority_name or 'critical' in priority_name else 0

            return matches * 3 + exact_bonus + priority_bonus

        # Sort by relevance score
        scored_issues = [(score_issue(issue), issue) for issue in final_issues]
        scored_issues.sort(reverse=True, key=lambda x: x[0])

        # --- Format results ---
        results = []
        for score, issue in scored_issues[:limit]:
            if score > 0:  # Only include issues with some relevance
                summary = issue.get('fields', {}).get('summary', 'No summary')
                key = issue.get('key', 'Unknown')
                results.append({
                    'title': f"{key}: {summary}",
                    'url': f"{JIRA_URL}/browse/{key}"
                })

        logger.info(f"JIRA search found {len(results)} relevant results")
        return results

    except Exception as e:
        logger.error(f"JIRA search error: {e}")
        return []

def search_confluence_docs(query, limit=5, space_key=None, debug=True):
    """
    Confluence search with fixes for poor indexing:
    - Drop stop words from queries
    - Force OR fallback if top results have low scores
    - Use broader field search (content, body)
    - Progressive loosening strategy
    """
    try:
        if not CONFLUENCE_TOKEN or not CONFLUENCE_EMAIL:
            logger.warning("Confluence credentials not configured - using fallback")
            return []

        # Stop words that break Confluence CQL
        STOP_WORDS = {'why', 'is', 'my', 'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'how', 'what', 'when', 'where', 'who'}

        def clean_words(words):
            """Remove stop words and short words"""
            return [w for w in words if len(w) > 2 and w.lower() not in STOP_WORDS]

        def run_search(cql):
            url = f"{CONFLUENCE_URL}/rest/api/search"
            params = {
                "cql": cql,
                "limit": limit * 10,   # pull more for debugging
                "expand": "title"
            }
            resp = requests.get(url, params=params, auth=(CONFLUENCE_EMAIL, CONFLUENCE_TOKEN), timeout=15)
            resp.raise_for_status()
            return resp.json().get("results", [])

        # --- Clean query words ---
        words = query.strip().split()
        clean_query_words = clean_words(words)

        # If we filtered out everything, use original words
        if not clean_query_words:
            clean_query_words = words

        logger.info(f"Original query: '{query}' -> Clean words: {clean_query_words}")

        # --- Build queries progressively ---
        cql_variants = []

        # 1. Exact phrase (standard fields)
        cql_variants.append(f'text ~ "\\"{query}\\"" OR title ~ "\\"{query}\\""')

        # 2. Clean words AND (standard fields)
        if len(clean_query_words) > 1:
            and_parts = [f'(title ~ "{w}" OR text ~ "{w}")' for w in clean_query_words]
            cql_variants.append(" AND ".join(and_parts))

        # 3. Clean words OR (standard fields)
        or_parts = [f'(title ~ "{w}" OR text ~ "{w}")' for w in clean_query_words]
        cql_variants.append(" OR ".join(or_parts))

        # 4. Single most important word (if we have multiple)
        if len(clean_query_words) > 1:
            # Use longest word as most likely to be significant
            main_word = max(clean_query_words, key=len)
            cql_variants.append(f'title ~ "{main_word}" OR text ~ "{main_word}"')

        # 5. Very broad fallback - just search for any word
        if clean_query_words:
            # Pick the most specific word (longest) and search broadly
            main_word = max(clean_query_words, key=len)
            cql_variants.append(f'text ~ "{main_word}"')

        # Add space filter if provided
        if space_key:
            cql_variants = [f'space.key = "{space_key}" AND ({c})' for c in cql_variants]

        # --- Try queries in order, with score quality check ---
        final_results = []
        for i, cql in enumerate(cql_variants):
            try:
                logger.info(f"Trying Confluence CQL #{i+1}: {cql}")
                results = run_search(cql)

                if results:
                    top_score = max(r.get("score", 0) for r in results[:3])
                    logger.info(f"Query #{i+1} returned {len(results)} results, top score: {top_score}")

                    # If we got decent results (score > 1) or this is our last attempt, use them
                    if top_score > 1 or i == len(cql_variants) - 1:
                        final_results = results
                        logger.info(f"Using results from query #{i+1}")
                        break
                    else:
                        logger.info(f"Top score {top_score} too low, trying next query...")

            except Exception as e:
                logger.error(f"Confluence query #{i+1} failed: {e}", exc_info=True)
                continue

        # --- Debug: log top 10 raw results ---
        if debug and final_results:
            logger.info("---- Raw Confluence Results ----")
            for r in final_results[:10]:
                page_id = r.get("content", {}).get("id")
                title = r.get("title") or "Untitled"
                score = r.get("score", 0)
                url = f"{CONFLUENCE_URL}/pages/{page_id}" if page_id else "N/A"
                logger.info(f"Title: {title} | Score: {score} | URL: {url}")
            logger.info("---- End Raw Results ----")

        # --- Re-rank: trust API score, tiny title nudge ---
        def score_fn(r):
            api_score = r.get("score", 0) or 0
            title = (r.get("title") or "").lower()

            # Check if any clean words appear in title
            title_word_matches = sum(1 for word in clean_query_words if word.lower() in title)
            boost = title_word_matches * 5  # Small boost per matching word

            return api_score * 100 + boost

        ranked = sorted(final_results, key=score_fn, reverse=True)

        # --- Format results ---
        formatted = []
        for r in ranked[:limit]:
            page_id = r.get("content", {}).get("id")
            title = r.get("title") or "Untitled"
            if not page_id:
                continue
            # Try multiple URL formats for Confluence page access
            page_url_options = [
                f"{CONFLUENCE_URL}/pages/viewpage.action?pageId={page_id}",
                f"{CONFLUENCE_URL}/display/~{page_id}",
                f"{CONFLUENCE_URL}/pages/{page_id}",
                f"https://blueshift.atlassian.net/wiki/spaces/~{page_id}"
            ]
            # Use the first format (most common)
            page_url = page_url_options[0]
            formatted.append({"title": title, "url": page_url})

        logger.info(f"Confluence search found {len(formatted)} results")
        return formatted

    except Exception as e:
        logger.error(f"Confluence search error: {e}", exc_info=True)
        return []

def search_zendesk_tickets(query, limit=5):
    """Search Zendesk tickets using API with improved error handling and recent date filtering"""
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

        # Calculate date for past 2 years
        from datetime import datetime, timedelta
        two_years_ago = datetime.now() - timedelta(days=730)
        date_filter = two_years_ago.strftime('%Y-%m-%d')

        # Search API endpoint
        url = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json"

        # Add date filtering to get recent tickets from past 2 years
        response = requests.get(url, headers=headers, params={
            'query': f'({query}) type:ticket created>={date_filter}',
            'per_page': limit,
            'sort_by': 'updated_at',
            'sort_order': 'desc'
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
                'per_page': 5  # Good balance for Help Center
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

    # Updated curated list with trigger troubleshooting and detailed platform navigation URLs
    help_docs_expanded = [
        {"title": "Campaign Studio - Journey Tab & Detail Mode", "url": "https://help.blueshift.com/hc/en-us/articles/4408704180499-Campaign-studio", "keywords": ["campaign", "studio", "journey", "detail", "mode", "trigger", "troubleshoot", "filter", "conditions"]},
        {"title": "User Journey in Campaign - Trigger Troubleshooting", "url": "https://help.blueshift.com/hc/en-us/articles/4408704006675-User-journey-in-a-campaign", "keywords": ["user", "journey", "trigger", "troubleshoot", "not", "sending", "evaluation", "filter", "conditions"]},
        {"title": "Trigger Actions - Platform Navigation", "url": "https://help.blueshift.com/hc/en-us/articles/4408725448467-Trigger-Actions", "keywords": ["trigger", "actions", "platform", "navigation", "check", "edit", "conditions"]},
        {"title": "Campaign Flow Control - Filter Configuration", "url": "https://help.blueshift.com/hc/en-us/articles/4408717301651-Campaign-flow-control", "keywords": ["flow", "control", "filters", "conditions", "trigger", "exit", "journey"]},
        {"title": "Journey Testing - Troubleshooting Guide", "url": "https://help.blueshift.com/hc/en-us/articles/4408718647059-Journey-testing", "keywords": ["journey", "testing", "troubleshoot", "debug", "trigger", "not", "working"]},
        {"title": "Campaign Execution Overview", "url": "https://help.blueshift.com/hc/en-us/articles/19600265288979-Campaign-execution-overview", "keywords": ["campaign", "execution", "troubleshoot", "trigger", "not", "sending", "issues"]},
        {"title": "Triggered Workflows - Configuration Steps", "url": "https://help.blueshift.com/hc/en-us/articles/4405437140115-Triggered-workflows", "keywords": ["triggered", "workflows", "configuration", "setup", "troubleshoot"]},
        {"title": "Event Triggered Campaigns - Transaction Setup", "url": "https://help.blueshift.com/hc/en-us/articles/360050760774-Transactions-in-event-triggered-campaigns", "keywords": ["event", "triggered", "transactions", "setup", "troubleshoot"]},
        {"title": "Editing, Pausing and Relaunching Campaigns", "url": "https://help.blueshift.com/hc/en-us/articles/6314649190291-Editing-Pausing-and-Relaunching-Campaigns", "keywords": ["editing", "pausing", "relaunching", "campaign", "troubleshoot", "issues"]},
        {"title": "Campaign Alerts - Monitoring Setup", "url": "https://help.blueshift.com/hc/en-us/articles/360047216553-Campaign-alerts", "keywords": ["campaign", "alerts", "monitoring", "troubleshoot", "not", "sending", "issues"]}
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

def search_blueshift_api_docs(query, limit=3):
    """Search Blueshift API documentation"""
    try:
        # Use WebFetch to get relevant API documentation
        import requests

        # Search the main API reference page with working endpoint URLs
        api_docs = [
            {"title": "Blueshift API Documentation - Overview", "url": "https://developer.blueshift.com/reference/welcome", "keywords": ["api", "developer", "documentation", "reference", "guide", "overview"]},
            {"title": "Events API - POST /api/v1/event", "url": "https://developer.blueshift.com/reference/post_api-v1-event", "keywords": ["events", "api", "custom", "attribute", "user", "tracking", "data", "event"]},
            {"title": "Customer API - POST /api/v1/customers", "url": "https://developer.blueshift.com/reference/post_api-v1-customers", "keywords": ["customer", "user", "profile", "custom", "attribute", "identify", "customers"]},
            {"title": "Customer Search API - GET /api/v1/customers", "url": "https://developer.blueshift.com/reference/get_api-v1-customers", "keywords": ["customer", "search", "user", "profile", "lookup"]},
            {"title": "Campaigns API - GET /api/v1/campaigns", "url": "https://developer.blueshift.com/reference/get_api-v1-campaigns", "keywords": ["campaigns", "api", "messaging", "email", "push", "sms"]},
            {"title": "Catalog API - POST /api/v1/catalog", "url": "https://developer.blueshift.com/reference/post_api-v1-catalog", "keywords": ["catalog", "products", "recommendations", "data"]},
        ]

        query_lower = query.lower()
        query_words = set(query_lower.split())

        # Score based on keyword matching
        scored_docs = []
        for doc in api_docs:
            score = 0

            # Title matching
            title_words = set(doc['title'].lower().split())
            title_matches = query_words.intersection(title_words)
            score += len(title_matches) * 5

            # Keyword matching
            keyword_words = set(' '.join(doc['keywords']).lower().split())
            keyword_matches = query_words.intersection(keyword_words)
            score += len(keyword_matches) * 3

            # Special scoring for specific terms
            if any(word in query_lower for word in ['custom', 'attribute']):
                if 'attribute' in doc['keywords']:
                    score += 10

            if 'api' in query_lower:
                if 'api' in doc['keywords']:
                    score += 5

            if score > 0:
                scored_docs.append((score, doc))

        # Sort by score and return top results
        scored_docs.sort(reverse=True, key=lambda x: x[0])
        results = [{"title": doc['title'], "url": doc['url']} for score, doc in scored_docs[:limit]]

        logger.info(f"Blueshift API docs search: '{query}' -> found {len(results)} results")
        return results

    except Exception as e:
        logger.error(f"Blueshift API docs search error: {e}")
        return []

def fetch_help_doc_content(url, max_content_length=3000):
    """Fetch actual content from help documentation URLs to extract step-by-step instructions"""
    try:
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.text, 'html.parser')

            # Remove scripts, styles, and navigation
            for tag in soup(['script', 'style', 'nav', 'header', 'footer']):
                tag.decompose()

            # Extract main content - try common content containers
            content_selectors = [
                'article', '.article-body', '.content', '.main-content',
                '.help-center-article', '.article-content', 'main'
            ]

            main_content = None
            for selector in content_selectors:
                main_content = soup.select_one(selector)
                if main_content:
                    break

            if not main_content:
                main_content = soup.body if soup.body else soup

            # Extract text and preserve structure
            text_content = main_content.get_text(separator='\n', strip=True)

            # Clean up excessive whitespace
            lines = [line.strip() for line in text_content.split('\n') if line.strip()]
            clean_content = '\n'.join(lines)

            # Limit length for AI processing
            if len(clean_content) > max_content_length:
                clean_content = clean_content[:max_content_length] + "...\n[Content truncated for length]"

            return clean_content
        return ""
    except Exception as e:
        logger.error(f"Error fetching help doc content from {url}: {e}")
        return ""

def generate_related_resources(query):
    """Generate contextually relevant resources using API searches with smart fallbacks and content fetching"""
    logger.info(f"Searching for resources: {query}")

    # Perform API searches with proper error handling
    help_docs = search_help_docs(query, limit=3)  # Limit to top 3 to fetch content
    confluence_docs = search_confluence_docs(query, limit=3)
    jira_tickets = search_jira_tickets(query, limit=3)
    support_tickets = search_zendesk_tickets(query, limit=3)
    api_docs = search_blueshift_api_docs(query, limit=2)  # Limit to top 2 for content fetching

    logger.info(f"Resource counts: help={len(help_docs)}, confluence={len(confluence_docs)}, jira={len(jira_tickets)}, zendesk={len(support_tickets)}, api_docs={len(api_docs)}")

    # Fetch actual content from ALL resources for comprehensive step-by-step instructions
    help_docs_with_content = []
    for doc in help_docs:
        content = fetch_help_doc_content(doc['url'])
        if content and len(content.strip()) > 50:  # Only include if substantial content
            help_docs_with_content.append({
                'title': doc['title'],
                'url': doc['url'],
                'content': content[:3000],  # More content for better instructions
                'source': 'help_docs'
            })

    # Fetch content from API docs too
    api_docs_with_content = []
    for doc in api_docs:
        content = fetch_help_doc_content(doc['url'])
        if content and len(content.strip()) > 50:
            api_docs_with_content.append({
                'title': doc['title'],
                'url': doc['url'],
                'content': content[:3000],
                'source': 'api_docs'
            })

    # Include Confluence docs with content if available
    confluence_docs_with_content = []
    for doc in confluence_docs:
        if doc.get('url'):
            content = fetch_help_doc_content(doc['url'])
            if content and len(content.strip()) > 50:
                confluence_docs_with_content.append({
                    'title': doc['title'],
                    'url': doc['url'],
                    'content': content[:3000],
                    'source': 'confluence'
                })

    # Combine ALL resources with content for comprehensive AI analysis
    platform_resources_with_content = help_docs_with_content + api_docs_with_content + confluence_docs_with_content

    logger.info(f"Content-rich resources: help_docs={len(help_docs_with_content)}, api_docs={len(api_docs_with_content)}, confluence={len(confluence_docs_with_content)}")

    # Include ticket summaries for context (without full content to save tokens)
    if jira_tickets:
        for ticket in jira_tickets[:2]:  # Top 2 JIRA tickets
            platform_resources_with_content.append({
                'title': ticket['title'],
                'url': ticket['url'],
                'content': f"Related JIRA ticket: {ticket['title']}",
                'source': 'jira'
            })

    if support_tickets:
        for ticket in support_tickets[:2]:  # Top 2 Zendesk tickets
            platform_resources_with_content.append({
                'title': ticket['title'],
                'url': ticket['url'],
                'content': f"Related support ticket: {ticket['title']}",
                'source': 'zendesk'
            })

    return {
        'help_docs': help_docs,
        'confluence_docs': confluence_docs,
        'jira_tickets': jira_tickets,
        'support_tickets': support_tickets,
        'api_docs': api_docs,
        'platform_resources': help_docs + api_docs,  # Original format for links
        'platform_resources_with_content': platform_resources_with_content  # With actual content for AI
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
    """Generate data insights using Athena queries based on user query with improved relevance"""
    try:
        # Same stop words filtering as other searches
        STOP_WORDS = {'why', 'is', 'my', 'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'how', 'what', 'when', 'where', 'who'}

        def clean_words(words):
            return [w for w in words if len(w) > 2 and w.lower() not in STOP_WORDS]

        # Extract key terms from user query
        words = user_query.strip().split()
        clean_query_words = clean_words(words)
        if not clean_query_words:
            clean_query_words = words

        logger.info(f"Athena query generation - Original: '{user_query}' -> Key terms: {clean_query_words}")

        # Get available tables first
        database_name = ATHENA_DATABASES[0]  # Use first database
        available_tables = get_available_tables(database_name)
        table_list = ', '.join(available_tables[:20]) if available_tables else "campaign_execution_v3"

        # Use AI to determine what kind of data query would be helpful
        headers = {
            'x-api-key': AI_API_KEY,
            'Content-Type': 'application/json',
            'anthropic-version': '2023-06-01'
        }

        # Simple prompt matching actual query patterns from examples
        analysis_prompt = f"""Generate a simple Athena SQL query for this Blueshift support question: "{user_query}"

Available database: {database_name}
Main table: campaign_execution_v3

Based on the user's question, create a SIMPLE query following these patterns:

For EXTERNAL FETCH errors:
select timestamp, user_uuid, campaign_uuid, trigger_uuid, message
from customer_campaign_logs.campaign_execution_v3
where account_uuid = 'your_account_uuid'
and campaign_uuid = 'your_campaign_uuid'
and user_uuid = 'your_user_uuid'
and log_level = 'ERROR'
and message LIKE '%ExternalFetchError%'
ORDER BY timestamp DESC

For other ERROR queries:
select timestamp, user_uuid, campaign_uuid, trigger_uuid, message
from customer_campaign_logs.campaign_execution_v3
where account_uuid = 'your_account_uuid'
and campaign_uuid = 'your_campaign_uuid'
and user_uuid = 'your_user_uuid'
and log_level = 'ERROR'
and message LIKE '%[error_term]%'
ORDER BY timestamp DESC

For general troubleshooting:
select timestamp, user_uuid, campaign_uuid, trigger_uuid, message
from customer_campaign_logs.campaign_execution_v3
where account_uuid = 'your_account_uuid'
and campaign_uuid = 'your_campaign_uuid'
and user_uuid = 'your_user_uuid'
ORDER BY timestamp DESC

RULES:
- Keep it SIMPLE - no complex OR conditions
- Use only ONE message LIKE condition
- Always include account_uuid, campaign_uuid, user_uuid placeholders
- Use ORDER BY timestamp DESC
- NO file_date conditions
- NO multiple OR clauses

Format your response as:
DATABASE: {database_name}

SQL_QUERY:
[Simple query with one message filter]

INSIGHT_EXPLANATION:
[Brief explanation of what this query will show]"""

        data = {
            'model': 'claude-3-5-sonnet-20241022',
            'max_tokens': 400,
            'messages': [{'role': 'user', 'content': analysis_prompt}]
        }

        response = requests.post('https://api.anthropic.com/v1/messages',
                               headers=headers, json=data, timeout=15)

        if response.status_code == 200:
            ai_response = response.json()['content'][0]['text'].strip()
            logger.info(f"Athena AI response: {ai_response[:200]}...")
            return parse_athena_analysis(ai_response, user_query)
        else:
            logger.error(f"Athena AI API error: {response.status_code}")
            return get_default_athena_insights(user_query)

    except Exception as e:
        logger.error(f"Athena insights generation error: {e}")
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

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.get_json()
        username = data.get('username', '')
        password = data.get('password', '')

        # Check credentials
        if username == 'Blueshift Support' and password == 'BlueS&n@*9072!':
            session['logged_in'] = True
            session.permanent = True
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Invalid username or password'})

    return render_template_string(LOGIN_TEMPLATE)

@app.route('/')
def index():
    # Check if user is logged in
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template_string(MAIN_TEMPLATE)

@app.route('/blueshift-favicon.png')
def favicon():
    """Serve the Blueshift favicon"""
    try:
        return send_file('blueshift-favicon.png', mimetype='image/png')
    except Exception as e:
        logger.error(f"Error serving favicon: {e}")
        return '', 404

@app.route('/favicon.ico')
def favicon_ico():
    """Serve favicon.ico (redirect to PNG)"""
    try:
        return send_file('blueshift-favicon.png', mimetype='image/png')
    except Exception as e:
        logger.error(f"Error serving favicon.ico: {e}")
        return '', 404

@app.route('/query', methods=['POST'])
def handle_query():
    # Check if user is logged in
    if not session.get('logged_in'):
        return jsonify({"error": "Authentication required"}), 401

    try:
        data = request.get_json()
        query = data.get('query', '').strip()

        if not query:
            return jsonify({"error": "Please provide a query"})

        # Generate AI-powered relevant resources first
        related_resources = generate_related_resources(query)

        # Call Anthropic API for high-quality response with platform resources content
        platform_resources_with_content = related_resources.get('platform_resources_with_content', [])
        if not platform_resources_with_content:
            # Fallback to resources without content
            platform_resources_with_content = related_resources.get('platform_resources', [])

        ai_response = call_anthropic_api(query, platform_resources_with_content)

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
    # Check if user is logged in
    if not session.get('logged_in'):
        return jsonify({"error": "Authentication required"}), 401

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
LOGIN_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Support Bot - Login</title>
    <link rel="icon" type="image/png" sizes="32x32" href="/blueshift-favicon.png">
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 400px;
            margin: 100px auto;
            padding: 20px;
            background: #f5f7fa;
        }
        .login-form {
            background: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        h1 {
            text-align: center;
            margin-bottom: 30px;
            color: #333;
            font-size: 24px;
            font-weight: 600;
        }
        .logo {
            height: 40px;
            vertical-align: middle;
            margin-right: 15px;
        }
        input {
            width: 100%;
            padding: 12px;
            margin: 10px 0;
            border: 1px solid #ddd;
            border-radius: 4px;
            box-sizing: border-box;
            font-size: 14px;
        }
        button {
            background-color: #2790FF;
            color: white;
            padding: 12px;
            width: 100%;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
            font-weight: 500;
            transition: background-color 0.3s ease;
        }
        button:hover {
            background-color: #1976d2;
        }
        .error {
            color: #d73527;
            margin: 10px 0;
            padding: 10px;
            background: #ffeaea;
            border-radius: 4px;
            display: none;
        }
    </style>
</head>
<body>
    <div class="login-form">
        <h1>
            <img src="/blueshift-favicon.png" alt="Blueshift" class="logo">
            Support Bot
        </h1>
        <form id="loginForm">
            <input type="text" id="username" placeholder="Username" required>
            <input type="password" id="password" placeholder="Password" required>
            <button type="submit">Login</button>
        </form>

        <div id="error" class="error"></div>
    </div>

    <script>
        document.getElementById('loginForm').addEventListener('submit', function(e) {
            e.preventDefault();

            const username = document.getElementById('username').value;
            const password = document.getElementById('password').value;
            const errorDiv = document.getElementById('error');

            // Send login request to server
            fetch('/login', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    username: username,
                    password: password
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    // Redirect to main app
                    window.location.href = '/';
                } else {
                    errorDiv.textContent = data.error || 'Invalid username or password';
                    errorDiv.style.display = 'block';
                    setTimeout(() => {
                        errorDiv.style.display = 'none';
                    }, 3000);
                }
            })
            .catch(error => {
                errorDiv.textContent = 'Login failed. Please try again.';
                errorDiv.style.display = 'block';
                setTimeout(() => {
                    errorDiv.style.display = 'none';
                }, 3000);
            });
        });

        // Clear error on input
        document.getElementById('username').addEventListener('input', function() {
            document.getElementById('error').style.display = 'none';
        });

        document.getElementById('password').addEventListener('input', function() {
            document.getElementById('error').style.display = 'none';
        });
    </script>
</body>
</html>
'''

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
            font-size: 1.2em;
            line-height: 1.3;
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
        <h1><img src="/blueshift-favicon.png" alt="Blueshift" style="height: 40px; vertical-align: middle; margin-right: 10px;">Blueshift Support Bot</h1>

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
                <h3>📚 Help Docs & APIs</h3>
                <ul>
                    <li>Official Blueshift help center articles</li>
                    <li>API documentation and endpoints</li>
                    <li>SDK integration guides</li>
                    <li>Setup and configuration instructions</li>
                </ul>
            </div>

            <div class="feature">
                <h3>🏢 Confluence</h3>
                <ul>
                    <li>Internal Confluence documentation</li>
                    <li>Team knowledge base articles</li>
                    <li>Troubleshooting runbooks</li>
                    <li>Engineering documentation</li>
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
                { key: 'jira_tickets', title: '🎫 JIRA Tickets', icon: '🎫' },
                { key: 'help_docs', title: '📚 Help Docs & APIs', icon: '📚' },
                { key: 'confluence_docs', title: '🏢 Confluence Pages', icon: '🏢' },
                { key: 'support_tickets', title: '🎯 Zendesk', icon: '🎯' }
            ];

            categories.forEach(category => {
                const categoryDiv = document.createElement('div');
                categoryDiv.className = 'source-category';
                categoryDiv.innerHTML = `<h4>${category.title}</h4>`;

                // For Help Docs, combine both help_docs and api_docs
                let items = [];
                if (category.key === 'help_docs') {
                    items = [...(resources['help_docs'] || []), ...(resources['api_docs'] || [])];
                } else {
                    items = resources[category.key] || [];
                }

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

    # Debug: Check environment variables
    print(f"\n=== Environment Variables Debug ===")
    print(f"JIRA_TOKEN: {'✅ Configured' if JIRA_TOKEN else '❌ Not set'}")
    print(f"JIRA_EMAIL: {'✅ Configured' if JIRA_EMAIL else '❌ Not set'}")
    print(f"CONFLUENCE_TOKEN: {'✅ Configured' if CONFLUENCE_TOKEN else '❌ Not set'}")
    print(f"CONFLUENCE_EMAIL: {'✅ Configured' if CONFLUENCE_EMAIL else '❌ Not set'}")
    print(f"ZENDESK_TOKEN: {'✅ Configured' if ZENDESK_TOKEN else '❌ Not set'}")
    print(f"ZENDESK_EMAIL: {'✅ Configured' if ZENDESK_EMAIL else '❌ Not set'}")
    print(f"ZENDESK_SUBDOMAIN: {'✅ Configured' if ZENDESK_SUBDOMAIN else '❌ Not set'}")
    print("=" * 40)

    app.run(host='0.0.0.0', port=port, debug=True)
