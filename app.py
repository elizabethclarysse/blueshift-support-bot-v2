from flask import Flask, request, jsonify, render_template_string, send_file, session, redirect, url_for
import requests
import os
import boto3
import json
from datetime import datetime, timedelta
import time
import base64
import logging
import re 

# Try to load .env file if it exists (for development/testing)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed, continue without it

app = Flask(__name__)
app.secret_key = 'blueshift_support_bot_secret_key_2023'
app.permanent_session_lifetime = timedelta(hours=12)

# --- GEMINI API CONFIGURATION ---
AI_API_KEY = os.environ.get('GEMINI_API_KEY')
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent"
# ---------------------------------

# AWS Athena configuration - set these via environment variables
ATHENA_DATABASES = os.environ.get('ATHENA_DATABASE', 'customer_campaign_logs').split(',')
ATHENA_S3_OUTPUT = os.environ.get('ATHENA_S3_OUTPUT', 's3://bsft-customers/athena-results/')
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


# --- FIX 2: Add Credential Validation at Startup ---
def validate_api_credentials_on_startup():
    """Test all API credentials at startup and log results"""
    validation_results = {}

    # Test JIRA
    if JIRA_TOKEN and JIRA_EMAIL and JIRA_URL:
        try:
            auth = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_TOKEN}".encode()).decode()
            headers = {'Authorization': f'Basic {auth}', 'Accept': 'application/json'}
            response = requests.get(f"{JIRA_URL}/rest/api/3/myself", headers=headers, timeout=10)
            validation_results['jira'] = response.status_code == 200
            if response.status_code != 200:
                logger.error(f"JIRA validation failed: {response.status_code} - {response.text[:200]}")
        except Exception as e:
            validation_results['jira'] = False
            logger.error(f"JIRA validation exception: {e}")
    else:
        validation_results['jira'] = False
        logger.error("JIRA credentials missing")

    # Test Confluence
    if CONFLUENCE_TOKEN and CONFLUENCE_EMAIL and CONFLUENCE_URL:
        try:
            response = requests.get(
                f"{CONFLUENCE_URL}/rest/api/user/current",
                auth=(CONFLUENCE_EMAIL, CONFLUENCE_TOKEN),
                timeout=10
            )
            validation_results['confluence'] = response.status_code == 200
            if response.status_code != 200:
                logger.error(f"Confluence validation failed: {response.status_code}")
        except Exception as e:
            validation_results['confluence'] = False
            logger.error(f"Confluence validation exception: {e}")
    else:
        validation_results['confluence'] = False
        logger.error("Confluence credentials missing")

    # Test Zendesk
    if ZENDESK_TOKEN and ZENDESK_EMAIL and ZENDESK_SUBDOMAIN:
        try:
            auth = base64.b64encode(f"{ZENDESK_EMAIL}/token:{ZENDESK_TOKEN}".encode()).decode()
            headers = {'Authorization': f'Basic {auth}', 'Accept': 'application/json'}
            response = requests.get(
                f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/users/me.json",
                headers=headers,
                timeout=10
            )
            validation_results['zendesk'] = response.status_code == 200
            if response.status_code != 200:
                logger.error(f"Zendesk validation failed: {response.status_code}")
        except Exception as e:
            validation_results['zendesk'] = False
            logger.error(f"Zendesk validation exception: {e}")
    else:
        validation_results['zendesk'] = False
        logger.error("ZENDESK credentials missing")

    logger.info(f"🔍 API Validation Results: {validation_results}")
    return validation_results

# Execute validation on startup
API_STATUS = validate_api_credentials_on_startup()
# --- END FIX 2 ---


# --- REPLACEMENT FOR call_anthropic_api, WITH AI RESPONSE FIX ---
def call_gemini_api(query, platform_resources=None, temperature=0.2):
    """Call Google Gemini API with system context and configuration.
    
    FIX: Max output tokens increased to 4000 to prevent MAX_TOKENS error.
    """
    if not AI_API_KEY:
        return "Error: GEMINI_API_KEY is not configured."

    try:
        headers = {
            'Content-Type': 'application/json',
        }
        
        # Build context from actual retrieved content
        platform_context = ""

        if platform_resources and len(platform_resources) > 0:
            resources_with_content = [r for r in platform_resources if isinstance(r, dict) and 'content' in r and len(r.get('content', '').strip()) > 50]

            if resources_with_content:
                platform_context = "\n\nDOCUMENTATION CONTENT FROM SEARCH RESULTS:\n"
                for i, resource in enumerate(resources_with_content[:4]):
                    platform_context += f"\n=== SOURCE {i+1}: {resource['title']} ===\n"
                    platform_context += f"URL: {resource['url']}\n"
                    platform_context += f"CONTENT:\n{resource['content'][:2000]}\n"
                    platform_context += "="*50 + "\n"
            else:
                platform_context = "\n\nRELEVANT RESOURCES FOUND (URLs only):\n"
                for i, resource in enumerate(platform_resources[:3]):
                    platform_context += f"{i+1}. {resource.get('title', 'Untitled')}\n   URL: {resource.get('url', 'N/A')}\n"

        # System Instruction content
        # FIX: Temperature set to 0.3 for the general query to reduce brittleness/conversational stops.
        if temperature > 0.0:
            temp = 0.3
        else:
            temp = temperature

        system_instruction_content = f"""You are Blueshift support helping troubleshoot customer issues. Your response MUST be comprehensive, actionable, and formatted using Markdown with proper bold formatting for readability.

INSTRUCTIONS:
1. **PRIORITY 1: Platform Navigation Steps.** Extract clear, numbered steps from the documentation content if available.
2. If documentation content contains step-by-step instructions, use them precisely.
3. If no specific steps in docs, provide general navigation based on Blueshift platform knowledge.
4. Combine documentation steps with your Blueshift platform knowledge for comprehensive guidance.
5. Focus on practical troubleshooting guidance.
6. **CRITICAL: Use bold markdown (**text**) for all section headers, key terms, important UI elements, menu paths, and button names to improve readability.**

RESPONSE FORMAT:

## **Feature Overview**
Explain what this feature/issue is about and how it relates to the Blueshift platform. Use **bold** for key concepts and feature names.

## **Platform Navigation Steps**
Based on the documentation above and Blueshift platform knowledge:

1. Navigate to **Menu Name** → **Submenu** → **Feature**
2. Use **bold** for all button names, field labels, and UI elements
3. Include specific **menu paths**, **button names**, and navigation instructions in bold

## **Troubleshooting Steps**
When this feature isn't working as expected:

1. **Platform Configuration Checks**
   - **Verify settings and required fields** - use bold for key actions
   - **Check user permissions and access**
   - **Confirm campaign/trigger status**

2. **Common Issues and Solutions**
   - **Typical problems** and their fixes (bold the problem type)
   - **Configuration errors** to look for
   - **Data flow issues** to investigate

3. **Advanced Debugging**
   - **Database queries:** customer_campaign_logs.campaign_execution_v3
   - **Error patterns:** ExternalFetchError, ChannelLimitError, DeduplicationError
   - **API endpoints** to test

## **Internal Notes**
- **Main troubleshooting database:** customer_campaign_logs.campaign_execution_v3
- **API Base:** https://api.getblueshift.com
- This is internal support guidance - provide actionable troubleshooting steps

FORMATTING RULES:
- Use **bold** for ALL section headers (even though they're already ## markdown headers)
- Use **bold** for key terms, concepts, feature names, and technical terms
- Use **bold** for UI elements: buttons, menus, fields, tabs, etc.
- Use **bold** for error types, status names, and system messages
- Use **bold** for database names, table names, and API endpoints
- This makes the response much easier to scan and read
"""

        # FIX: Ensure the prompt explicitly tells the model to start the structured response
        start_instruction = "Start your response immediately using the RESPONSE FORMAT provided below. Do NOT use conversational filler like 'Of course, here is...'"
        full_prompt = system_instruction_content + "\n\n" + start_instruction + "\n\n---\n\nSUPPORT QUERY: " + query + "\n" + platform_context

        contents_array = [
            {"role": "user", "parts": [{"text": full_prompt}]}
        ]

        data = {
            "contents": contents_array,
            "generationConfig": { 
                "temperature": temp, 
                "maxOutputTokens": 4000  # CRITICAL FIX: Increased capacity
            }
        }
        
        # Add API Key to the URL
        url_with_key = f"{GEMINI_API_URL}?key={AI_API_KEY}"

        # Timeout increased to 60 seconds 
        response = requests.post(url_with_key, headers=headers, json=data, timeout=60) 

        if response.status_code == 200:
            response_json = response.json()
            # Safely extract the text from the response structure
            gemini_response = response_json.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '').strip()
            if not gemini_response:
                # Check for prompt filtering or safety block issues
                return f"API Error: Response blocked or empty. Reason: {response_json.get('candidates', [{}])[0].get('finishReason')}"
            return gemini_response
        else:
            return f"API Error {response.status_code}: {response.text[:200]}"

    except Exception as e:
        return f"Error: {str(e)}"
# --- END REPLACEMENT ---


def generate_followup_suggestions(original_query, ai_response):
    """Generate 3 relevant follow-up questions based on the query and response."""
    if not AI_API_KEY:
        return []

    try:
        prompt = f"""Based on this support query and response, generate exactly 3 short, relevant follow-up questions that a user might want to ask next.

ORIGINAL QUERY: {original_query}

AI RESPONSE: {ai_response[:1000]}

Generate 3 natural follow-up questions that:
1. Ask for more specific details or clarification
2. Explore related features or troubleshooting steps
3. Request practical examples or best practices

Format your response as ONLY 3 questions, one per line, with no numbering, bullets, or extra text.
Each question should be concise (max 10 words).

Example format:
How do I configure this in the UI?
What are common errors with this feature?
Can you show me an example implementation?"""

        headers = {'Content-Type': 'application/json'}
        contents_array = [{"role": "user", "parts": [{"text": prompt}]}]
        data = {
            "contents": contents_array,
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 300
            }
        }

        url_with_key = f"{GEMINI_API_URL}?key={AI_API_KEY}"
        response = requests.post(url_with_key, headers=headers, json=data, timeout=15)

        if response.status_code == 200:
            response_json = response.json()
            text = response_json.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '').strip()

            if text:
                # Parse the response into individual questions
                questions = [q.strip() for q in text.split('\n') if q.strip() and len(q.strip()) > 10]
                # Return up to 3 questions
                return questions[:3]

        return []

    except Exception as e:
        logger.error(f"Error generating follow-up suggestions: {e}")
        return []


# --- FIX: JIRA Search - Switched to GET request for reliability ---
def search_jira_tickets_improved(query, limit=5, debug=True):
    """FIXED: Switched JIRA search from POST to GET with JQL in params for higher reliability, avoiding 410 error."""
    try:
        if not API_STATUS.get('jira', False):
            logger.warning("JIRA API not available - skipping search")
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
        }

        # --- Clean query words ---
        words = query.strip().split()
        clean_query_words = clean_words(words)

        if not clean_query_words:
            clean_query_words = words

        logger.info(f"JIRA search - Original: '{query}' -> Clean words: {clean_query_words}")

        # --- Build JQL queries progressively for GET request ---
        jql_variants = []

        # 1. Exact phrase (highest relevance)
        jql_variants.append(f'summary ~ "\\"{query}\\"" ORDER BY updated DESC')

        # 2. All clean words AND in summary and text (better relevance than OR)
        if len(clean_query_words) > 1:
            and_parts = [f'(summary ~ "{w}" OR text ~ "{w}")' for w in clean_query_words]
            jql_variants.append(f'({" AND ".join(and_parts)}) ORDER BY updated DESC')

        # 3. Most important words (fallback) - use the two longest/most significant words
        if len(clean_query_words) >= 2:
            important_words = sorted(clean_query_words, key=len, reverse=True)[:2]
            important_parts = [f'(summary ~ "{w}" OR text ~ "{w}")' for w in important_words]
            jql_variants.append(f'({" AND ".join(important_parts)}) ORDER BY updated DESC')

        # 4. Clean words OR in summary and text (most reliable for finding results but least relevant)
        or_parts = [f'(summary ~ "{w}" OR text ~ "{w}")' for w in clean_query_words]
        jql_variants.append(f'({" OR ".join(or_parts)}) ORDER BY updated DESC')

        # Use the correct JIRA v3 API endpoint for JQL queries
        url = f"{JIRA_URL}/rest/api/3/search/jql"

        # --- Try queries in order ---
        final_issues = []
        for i, jql in enumerate(jql_variants):
            try:
                logger.info(f"Trying JIRA JQL #{i+1} (GET): {jql}")

                params = {
                    'jql': jql,
                    'maxResults': limit * 3,
                    'fields': 'summary,key,status,priority,issuetype'
                }

                # Use the correct v3 API endpoint with GET request
                response = requests.get(url, headers=headers, params=params, timeout=15) 

                if response.status_code == 200:
                    data = response.json()
                    issues = data.get('issues', [])

                    if issues:
                        logger.info(f"JIRA query #{i+1} returned {len(issues)} results. Breaking.")
                        final_issues = issues
                        break
                    else:
                        logger.info(f"JIRA query #{i+1} returned no results")
                else:
                    # Log the failed JIRA endpoint search error
                    logger.error(f"JIRA API error on query #{i+1} (GET): {response.status_code} - {response.text[:200]}")

            except Exception as e:
                logger.error(f"JIRA query #{i+1} failed: {e}")
                continue

        if not final_issues:
            logger.info("No JIRA results found with any query variant")
            return []

        # --- Score and filter results ---
        def score_issue(issue):
            summary = issue.get('fields', {}).get('summary', '').lower()

            # Count partial word matches in summary (more lenient)
            matches = sum(1 for word in clean_query_words if word.lower() in summary)

            # Bonus for exact phrase match
            exact_bonus = 50 if query.lower() in summary else 0

            # Bonus for multiple word matches (AND logic preference)
            if len(clean_query_words) > 1:
                match_ratio = matches / len(clean_query_words)
                completeness_bonus = int(match_ratio * 20)  # Up to 20 points for having all words
            else:
                completeness_bonus = 0

            # Priority bonus (less important than relevance)
            priority = issue.get('fields', {}).get('priority', {})
            priority_name = priority.get('name', '').lower() if priority else ''
            priority_bonus = 3 if 'high' in priority_name or 'critical' in priority_name else 0

            # Issue type bonus (prefer certain types)
            issue_type = issue.get('fields', {}).get('issuetype', {})
            issue_type_name = issue_type.get('name', '').lower() if issue_type else ''
            type_bonus = 5 if 'bug' in issue_type_name or 'support' in issue_type_name else 0

            # Minimum base score for relevant-sounding tickets
            base_score = 5 if ('facebook' in summary or 'syndication' in summary or 'audience' in summary) else 0

            total_score = (matches * 10) + exact_bonus + completeness_bonus + priority_bonus + type_bonus + base_score
            return total_score

        # Sort by relevance score
        scored_issues = [(score_issue(issue), issue) for issue in final_issues]
        scored_issues.sort(reverse=True, key=lambda x: x[0])

        # Debug log top scoring issues
        logger.info(f"JIRA scoring results:")
        for i, (score, issue) in enumerate(scored_issues[:5]):
            summary = issue.get('fields', {}).get('summary', 'No summary')
            key = issue.get('key', 'Unknown')
            logger.info(f"  {i+1}. Score: {score} - {key}: {summary[:50]}...")

        # --- Format results ---
        results = []
        for score, issue in scored_issues[:limit]:
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
# --- END FIX ---

# --- FIX: Confluence Search - Bypassed Validation for Raw Results ---
def search_confluence_docs_improved(query, limit=5, space_key=None, debug=True):
    """
    FIXED: Confluence search logic. Returns raw results, relying on central validation.
    """
    try:
        if not API_STATUS.get('confluence', False):
            logger.warning("Confluence API not available - skipping search")
            return []

        # Stop words that break Confluence CQL
        STOP_WORDS = {'why', 'is', 'my', 'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'how', 'what', 'when', 'where', 'who'}

        def clean_words(words):
            """Remove stop words and short words"""
            return [w for w in words if len(w) > 2 and w.lower() not in STOP_WORDS]

        def run_search(cql):
            url = f"{CONFLUENCE_URL}/rest/api/content/search"
            params = {
                "cql": cql,
                "limit": limit * 10,   # pull more for debugging
                "expand": "content"
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
        or_parts.append(f'content ~ "{query}"')
        cql_variants.append(" OR ".join(or_parts))


        # 4. Single most important word (if we have multiple)
        if len(clean_query_words) > 1:
            # Use longest word as most likely to be significant
            main_word = max(clean_query_words, key=len)
            cql_variants.append(f'title ~ "{main_word}" OR text ~ "{main_word}"')

        # Add space filter if provided
        if space_key:
            cql_variants = [f'space.key = "{space_key}" AND ({c})' for c in cql_variants]

        # --- Try queries in order ---
        final_results = []
        for i, cql in enumerate(cql_variants):
            try:
                logger.info(f"Trying Confluence CQL #{i+1}: {cql}")
                results = run_search(cql)

                if results:
                    final_results = results
                    logger.info(f"Query #{i+1} returned {len(results)} results. Using these results.")
                    break
                else:
                    logger.info(f"Query #{i+1} returned no results, trying next query...")

            except requests.exceptions.HTTPError as http_e:
                 logger.error(f"Confluence query #{i+1} failed HTTP: {http_e.response.status_code} - {http_e.response.text[:100]}", exc_info=True)
                 continue
            except Exception as e:
                logger.error(f"Confluence query #{i+1} failed: {e}", exc_info=True)
                continue

        if not final_results:
             logger.info("No Confluence results found with any query variant")
             return []

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
            # Try multiple ways to get the page ID due to different Confluence API response formats
            page_id = r.get("content", {}).get("id") or r.get("id")
            title = r.get("title") or "Untitled"

            # Debug log the structure of results that don't have page_id
            if not page_id:
                logger.warning(f"Confluence result missing page_id: {r.keys()} - title: {title}")
                # Try to construct URL using other fields if available
                if "url" in r:
                    page_url = r["url"]
                elif "_links" in r and "webui" in r["_links"]:
                    page_url = f"{CONFLUENCE_URL.rstrip('/wiki')}{r['_links']['webui']}"
                else:
                    continue  # Skip if we can't get a URL
            else:
                # Use the most common and reliable URL format
                page_url = f"{CONFLUENCE_URL}/pages/viewpage.action?pageId={page_id}"

            formatted.append({"title": title, "url": page_url})

        logger.info(f"Confluence search found {len(formatted)} results")
        
        # Returning raw results to be validated centrally in generate_related_resources_improved
        return formatted
    
    except Exception as e:
        logger.error(f"Confluence search error: {e}", exc_info=True)
        return []
# --- END FIX ---


# --- FIX 3: Simplified search_zendesk_tickets (Kept same) ---
def search_zendesk_tickets_improved(query, limit=5):
    """Simplified Zendesk search, using API_STATUS"""
    if not API_STATUS.get('zendesk', False):
        logger.warning("Zendesk API not available - skipping search")
        return []

    try:
        # Assuming token auth is used via ZENDESK_EMAIL/token
        auth = base64.b64encode(f"{ZENDESK_EMAIL}/token:{ZENDESK_TOKEN}".encode()).decode()
        headers = {
            'Authorization': f'Basic {auth}',
            'Accept': 'application/json'
        }

        url = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json"
        params = {
            'query': f'({query}) type:ticket',
            'per_page': limit,
            'sort_by': 'updated_at',
            'sort_order': 'desc'
        }

        response = requests.get(url, headers=headers, params=params, timeout=20)

        if response.status_code == 200:
            data = response.json()
            results = []
            
            for ticket in data.get('results', []):
                subject = ticket.get('subject', 'No Subject')
                ticket_id = ticket.get('id')
                
                results.append({
                    'title': f"Ticket #{ticket_id}: {subject}",
                    'url': f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/agent/tickets/{ticket_id}"
                })
            
            logger.info(f"Zendesk search returned {len(results)} results for '{query}'")
            return results
        else:
            logger.error(f"ZENDESK search failed: {response.status_code} - {response.text[:200]}")
            return []
            
    except Exception as e:
        logger.error(f"Zendesk search exception: {e}")
        return []
# --- END FIX 3 ---


def search_help_docs(query, limit=3):
    """IMPROVED help docs search with better trigger/mobile coverage (using curated list as fallback)"""
    try:
        # Try API search first
        if ZENDESK_SUBDOMAIN and ZENDESK_TOKEN and API_STATUS.get('zendesk', False):
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

            search_url = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/help_center/articles/search.json"
            response = requests.get(search_url, headers=headers, params={
                'query': query,
                'per_page': 8  # Get more results
            }, timeout=15)

            if response.status_code == 200:
                data = response.json()
                results = []
                for article in data.get('results', []):
                    title = article.get('title', 'Untitled')
                    url = article.get('html_url', '')
                    if title and url:
                        results.append({'title': title, 'url': url})

                if results:
                    logger.info(f"Help Center API found {len(results)} results")
                    return results[:limit]
            else:
                 logger.warning(f"Zendesk Help Center API failed: {response.status_code}")

    except Exception as e:
        logger.error(f"Help Center API search error: {e}")

    # EXPANDED curated list logic (kept as solid fallback)
    help_docs_expanded = [
        {"title": "Campaign Studio - Journey Tab & Detail Mode", "url": "https://help.blueshift.com/hc/en-us/articles/4408704180499-Campaign-studio", "keywords": ["campaign", "studio", "journey", "detail", "mode", "trigger", "troubleshoot", "filter", "conditions", "navigation"]},
        {"title": "User Journey in Campaign - Trigger Troubleshooting", "url": "https://help.blueshift.com/hc/en-us/articles/4408704006675-User-journey-in-a-campaign", "keywords": ["user", "journey", "trigger", "troubleshoot", "not", "sending", "evaluation", "filter", "conditions"]},
        {"title": "Triggered Campaigns - Setup and Configuration", "url": "https://help.blueshift.com/hc/en-us/articles/4405437140115-Triggered-workflows", "keywords": ["triggered", "campaigns", "workflows", "configuration", "setup", "automation", "troubleshoot", "not", "working"]},
        {"title": "Event Triggered Campaigns", "url": "https://help.blueshift.com/hc/en-us/articles/360050760774-Transactions-in-event-triggered-campaigns", "keywords": ["event", "triggered", "campaigns", "transactions", "setup", "troubleshoot", "not", "firing"]},
        {"title": "Trigger Actions and Conditions", "url": "https://help.blueshift.com/hc/en-us/articles/4408725448467-Trigger-Actions", "keywords": ["trigger", "actions", "conditions", "platform", "navigation", "check", "edit", "setup"]},
        {"title": "Campaign Flow Control and Filters", "url": "https://help.blueshift.com/hc/en-us/articles/4408717301651-Campaign-flow-control", "keywords": ["flow", "control", "filters", "conditions", "trigger", "exit", "journey", "not", "working"]},
        {"title": "Journey Testing and Debugging", "url": "https://help.blueshift.com/hc/en-us/articles/4408718647059-Journey-testing", "keywords": ["journey", "testing", "troubleshoot", "debug", "trigger", "not", "working", "preview", "test"]},
        {"title": "Campaign Execution and Troubleshooting", "url": "https://help.blueshift.com/hc/en-us/articles/19600265288979-Campaign-execution-overview", "keywords": ["campaign", "execution", "troubleshoot", "trigger", "not", "sending", "issues", "monitoring"]},
        {"title": "Mobile Push Notifications", "url": "https://help.blueshift.com/hc/en-us/articles/115002714413-Push-notifications", "keywords": ["mobile", "push", "notifications", "app", "trigger", "cloud", "messaging", "setup"]},
        {"title": "In-App Messages Setup", "url": "https://help.blueshift.com/hc/en-us/articles/360043199611-In-app-messages", "keywords": ["in-app", "messages", "mobile", "app", "trigger", "cloud", "setup", "configuration"]},
        {"title": "Mobile SDK Integration", "url": "https://help.blueshift.com/hc/en-us/articles/360043199451-Mobile-SDK", "keywords": ["mobile", "sdk", "integration", "app", "trigger", "cloud", "setup", "configuration"]},
        {"title": "Email Campaign Creation", "url": "https://help.blueshift.com/hc/en-us/articles/115002714173-Email-campaigns", "keywords": ["email", "campaign", "create", "setup", "subject", "line", "personalization", "template"]},
        {"title": "Personalization and Dynamic Content", "url": "https://help.blueshift.com/hc/en-us/articles/115002714253-Personalization", "keywords": ["personalization", "dynamic", "content", "subject", "line", "custom", "attributes", "merge"]},
        {"title": "Segmentation Overview", "url": "https://help.blueshift.com/hc/en-us/articles/115002669413-Segmentation-overview", "keywords": ["segmentation", "audience", "targeting", "segments", "customer", "groups", "filters"]},
        {"title": "Facebook Conversions API", "url": "https://help.blueshift.com/hc/en-us/articles/24009984649235-Facebook-Conversions-API", "keywords": ["facebook", "conversions", "api", "integration", "tracking", "audience", "syndication", "setup"]},
        {"title": "Facebook Audience Syndication", "url": "https://help.blueshift.com/hc/en-us/articles/360046864473-Facebook-audience", "keywords": ["facebook", "audience", "syndication", "lookalike", "custom", "integration", "setup", "troubleshoot"]},
        {"title": "External Fetch Configuration", "url": "https://help.blueshift.com/hc/en-us/articles/360006449754-External-fetch", "keywords": ["external", "fetch", "api", "integration", "troubleshoot", "error", "failed", "configuration"]},
        {"title": "Webhook Integration Setup", "url": "https://help.blueshift.com/hc/en-us/articles/115002714333-Webhooks", "keywords": ["webhook", "integration", "api", "external", "setup", "troubleshoot", "failed", "configuration"]},
    ]

    query_lower = query.lower()
    query_words = set(query_lower.split())

    stop_words = {'the', 'a', 'an', 'and', 'or', 'but'}
    clean_query_words = [w for w in query_words if w not in stop_words and len(w) > 1]

    scored_docs = []
    for doc in help_docs_expanded:
        score = 0
        title_words = set(doc['title'].lower().split())
        title_matches = len([w for w in clean_query_words if w in title_words])
        score += title_matches * 8

        keyword_words = set(' '.join(doc['keywords']).lower().split())
        keyword_matches = len([w for w in clean_query_words if w in keyword_words])
        score += keyword_matches * 4

        if 'trigger' in clean_query_words:
            if 'trigger' in doc['keywords']:
                score += 15 
            if any(word in clean_query_words for word in ['app', 'mobile', 'cloud']):
                if any(word in doc['keywords'] for word in ['mobile', 'app', 'push', 'cloud']):
                    score += 10

        if any(word in clean_query_words for word in ['not', 'troubleshoot', 'debug', 'help', 'issue']):
            if any(word in doc['keywords'] for word in ['troubleshoot', 'not', 'working', 'debug']):
                score += 8

        if score > 0:
            scored_docs.append((score, doc))

    scored_docs.sort(reverse=True, key=lambda x: x[0])
    results = [doc for score, doc in scored_docs[:limit]]

    logger.info(f"Help docs search (fallback): '{query}' -> found {len(results)} results")
    return results

def search_blueshift_api_docs(query, limit=3):
    """Search Blueshift API documentation (kept same)"""
    try:
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

# --- FIX 4: Robust fetch_help_doc_content with BeautifulSoup Fallback (Kept same) ---
def fetch_help_doc_content_improved(url, max_content_length=2000):
    """Improved content fetching with fallback for missing BeautifulSoup"""
    try:
        logger.info(f"Fetching content from: {url}")

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        response = requests.get(url, timeout=15, headers=headers)
        if response.status_code != 200:
            logger.warning(f"Failed to fetch {url}: Status {response.status_code}")
            return ""

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Remove unwanted elements
            for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'form']):
                tag.decompose()

            # Find main content using common selectors
            main_content = soup.select_one('article .article-body') or soup.select_one('.article-content') or soup.find('article') or soup.body or soup
            
            # Extract text with structure
            text_content = ""
            for element in main_content.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li']):
                text = element.get_text(strip=True)
                if text and len(text) > 15:
                    if element.name.startswith('h'):
                        text_content += f"\n[HEADING] {text}\n"
                    else:
                        text_content += f"{text}\n"
            
            clean_content = '\n'.join([line.strip() for line in text_content.split('\n') if line.strip()])
            
            if len(clean_content) > max_content_length:
                clean_content = clean_content[:max_content_length] + "...[truncated]"
            
            logger.info(f"Successfully extracted {len(clean_content)} characters using BeautifulSoup")
            return clean_content
            
        except ImportError:
            # Fallback: simple text extraction without BeautifulSoup
            logger.warning("BeautifulSoup not available - using simple text extraction")
            
            # Basic HTML stripping (not perfect but functional)
            text = response.text
            
            # Remove scripts and styles
            text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
            
            # Remove HTML tags
            text = re.sub(r'<[^>]+>', ' ', text)
            
            # Clean up whitespace
            text = re.sub(r'\s+', ' ', text).strip()
            
            if len(text) > max_content_length:
                text = text[:max_content_length] + "...[truncated]"
            
            logger.info(f"Fallback extraction: {len(text)} characters")
            return text

    except Exception as e:
        logger.error(f"Error fetching content from {url}: {e}")
        return ""
# --- END FIX 4 ---

# --- CRITICAL FIX: TRULY LENIENT Validation Function ---
def validate_search_results_improved(query, results, source_name):
    """TRULY LENIENT validation - Accept most results unless entirely irrelevant."""
    if not results:
        return []

    query_lower = query.lower()
    query_words = set(query.lower().split()) # FIX: ensure the query words are lowercased here
    validated_results = []

    # Remove only the most basic stop words - keep more meaningful words
    stop_words = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should'}
    clean_query_words = [w for w in query_words if w not in stop_words and len(w) > 2]

    for result in results:
        title = result.get('title', '').lower()
        url = result.get('url', '')
        # Also check description/summary if available
        description = result.get('description', '').lower()
        summary = result.get('summary', '').lower()
        content = f"{title} {description} {summary}".lower()

        # Set default to ACCEPT (the core fix)
        should_include = True

        # Check for ANY relevance in title OR content
        meaningful_matches = len([w for w in clean_query_words if w in content])

        # Special handling for Confluence - be extremely lenient since Confluence search already did relevance filtering
        if source_name == "Confluence":
            should_include = True  # Accept all Confluence results since they passed Confluence's own relevance filtering
        elif source_name == "JIRA":
            # Be very lenient with JIRA results since they're already scored and filtered
            should_include = True  # Accept JIRA results that made it through the scoring system
        elif meaningful_matches == 0:
            # Expanded platform terms for better context matching
            blueshift_terms = {'campaign', 'trigger', 'api', 'event', 'customer', 'journey', 'studio', 'message', 'mobile', 'app', 'push', 'zendesk', 'jira', 'confluence', 'facebook', 'audience', 'lookalike', 'syndication', 'integration', 'external', 'fetch', 'optimizer', 'email', 'sms', 'segment', 'webhook', 'personalization', 'recommendation', 'error', 'failed', 'limit', 'channel', 'delivery', 'bounce'}
            has_blueshift = any(term in content for term in blueshift_terms)

            if not has_blueshift:
                should_include = False  # Only reject if truly irrelevant

        if should_include and url:  # Must have valid URL
            validated_results.append(result)
            logger.info(f"✅ {source_name} - Included: {result.get('title', 'Untitled')[:60]}...")
        else:
            logger.info(f"❌ {source_name} - Excluded: {result.get('title', 'Untitled')[:60]}...")
            
    logger.info(f"{source_name} validation: {len(results)} → {len(validated_results)} results")
    return validated_results
# --- END CRITICAL FIX ---

def verify_step_extraction(query, resources_with_content):
    """Verify if actual step-by-step instructions exist in the content (kept for completeness)"""
    # This function is not used in the current flow, but kept in case it is reintroduced.
    pass

# --- FIX 5: Update Main Resource Generation Function (Kept same, calls updated searches) ---
def generate_related_resources_improved(query):
    """Improved resource generation with better validation and search calls"""
    logger.info(f"🔍 Searching for resources: '{query}'")

    # Perform searches with restored/improved functions
    help_docs = validate_search_results_improved(query, search_help_docs(query, limit=4), "Help Docs")
    
    # We apply validation manually on the Confluence raw results for troubleshooting,
    # then include them all if they were found (to debug the Confluence validation step)
    confluence_raw = search_confluence_docs_improved(query, limit=4)
    # FIX: Confluence validation is now run, but because the base filter is so strict, 
    # we rely on it now being correctly filtered.
    confluence_docs = validate_search_results_improved(query, confluence_raw, "Confluence")

    jira_tickets = validate_search_results_improved(query, search_jira_tickets_improved(query, limit=4), "JIRA")
    support_tickets = validate_search_results_improved(query, search_zendesk_tickets_improved(query, limit=4), "Zendesk")
    api_docs = validate_search_results_improved(query, search_blueshift_api_docs(query, limit=3), "API Docs")

    logger.info(f"📊 Final counts: Help={len(help_docs)}, Confluence={len(confluence_docs)}, JIRA={len(jira_tickets)}, Zendesk={len(support_tickets)}, API={len(api_docs)}")

    # Fetch content from top results
    resources_with_content = []

    # Prioritize help docs and API docs for content fetching
    priority_resources = help_docs[:2] + api_docs[:2] + confluence_docs[:1] # Include 1 top confluence doc

    # Use the improved content fetching function
    for doc in priority_resources:
        if doc.get('url'):
            content = fetch_help_doc_content_improved(doc['url'])
            if content and len(content.strip()) > 50:  # Must have meaningful content
                resources_with_content.append({
                    'title': doc['title'],
                    'url': doc['url'],
                    'content': content,
                    'source': 'help_docs' if doc in help_docs else ('confluence' if doc in confluence_docs else 'api_docs')
                })
                logger.info(f"✅ Fetched content: {doc['title'][:60]}... ({len(content)} chars)")

    # Add JIRA and Zendesk details without full fetch
    for ticket in jira_tickets[:2]:
        if ticket.get('url'):
            ticket_content = f"JIRA Ticket: {ticket['title']}\nThis engineering ticket may contain platform navigation steps or UI element references."
            resources_with_content.append({
                'title': ticket['title'],
                'url': ticket['url'],
                'content': ticket_content,
                'source': 'jira'
            })

    for ticket in support_tickets[:2]:
        if ticket.get('url'):
            ticket_content = f"Support Ticket: {ticket['title']}\nThis support ticket may contain step-by-step platform navigation instructions provided by agents."
            resources_with_content.append({
                'title': ticket['title'],
                'url': ticket['url'],
                'content': ticket_content,
                'source': 'zendesk'
            })

    logger.info(f"📄 Resources with content: {len(resources_with_content)}")

    return {
        'help_docs': help_docs,
        'confluence_docs': confluence_docs,
        'jira_tickets': jira_tickets,
        'support_tickets': support_tickets,
        'api_docs': api_docs,
        'platform_resources_with_content': resources_with_content
    }
# --- END FIX 5 ---

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
    # NOTE: This function is not used for the AI workflow, only for manual user data lookup.
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

            # Note: The output showed Access Denied here. The fix is external (AWS permissions),
            # but we ensure the error message is clear.
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
    """Keep the query as a template with placeholder values - do not substitute real data"""

    # Keep placeholder values for account UUIDs and other sensitive data
    # Replace any real UUIDs that might have been inserted with placeholders
    import re

    # Replace any real UUID patterns with placeholder text
    uuid_pattern = r'[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}'
    customized = re.sub(uuid_pattern, 'client_account_uuid', sql_query)

    # Ensure placeholder values are used for common fields
    customized = customized.replace('11d490bf-b250-4749-abf4-b6197620a985', 'client_account_uuid')

    # Use example placeholder dates instead of real dates - fix pattern matching
    import re
    # Replace file_date patterns with fixed example date
    customized = re.sub(r"file_date >= '[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}'", "file_date >= '2024-12-01'", customized)
    customized = re.sub(r"file_date < '[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}'", "file_date < '2024-12-15'", customized)

    # Ensure other sensitive fields use placeholders
    if 'user_uuid' in customized and 'client_user_uuid' not in customized:
        customized = re.sub(r"user_uuid = '[^']*'", "user_uuid = 'client_user_uuid'", customized)
    if 'campaign_uuid' in customized and 'client_campaign_uuid' not in customized:
        customized = re.sub(r"campaign_uuid = '[^']*'", "campaign_uuid = 'client_campaign_uuid'", customized)
    if 'trigger_uuid' in customized and 'client_trigger_uuid' not in customized:
        customized = re.sub(r"trigger_uuid = '[^']*'", "trigger_uuid = 'client_trigger_uuid'", customized)

    return customized

def get_available_tables(database_name):
    """Get list of available tables in the database - returns hardcoded list to avoid S3 permission issues"""
    try:
        # Return a hardcoded list of known tables to avoid executing queries that require S3 permissions
        if database_name == 'customer_campaign_logs':
            return ['campaign_execution_v3', 'user_events', 'campaign_metrics', 'email_events', 'sms_events']
        else:
            return ['campaign_execution_v3']  # Default fallback
    except Exception as e:
        print(f"Error getting tables: {e}")
        return []

# Cache for common message patterns - instant lookup, no database query needed
MESSAGE_PATTERN_CACHE = {
    # Quiet hours
    'quiet': 'QuietHours',
    'hours': 'QuietHours',

    # Facebook/Social
    'facebook': 'FacebookAudienceSync',
    'fb': 'FacebookAudienceSync',
    'sync': 'FacebookAudienceSync',
    'audience': 'FacebookAudienceSync',
    'lookalike': 'FacebookAudienceSync',
    'syndication': 'FacebookAudienceSync',

    # Errors
    'external': 'ExternalFetchError',
    'fetch': 'ExternalFetchError',
    'channel': 'ChannelLimitError',
    'limit': 'ChannelLimitError',
    'dedup': 'DeduplicationError',
    'dedupe': 'DeduplicationError',
    'deduplication': 'DeduplicationError',
    'duplicate': 'DeduplicationError',
    'bounce': 'SoftBounce',
    'bounced': 'SoftBounce',

    # Triggers & Journey
    'trigger': 'TriggerEvaluation',
    'triggered': 'TriggerEvaluation',
    'journey': 'UserJourney',
    'evaluation': 'TriggerEvaluation',

    # Suppression & Opt-out
    'suppression': 'SuppressionCheck',
    'suppressed': 'SuppressionCheck',
    'optout': 'OptOutCheck',
    'unsubscribe': 'UnsubscribeCheck',

    # Channels
    'sms': 'SMSDelivery',
    'email': 'EmailDelivery',
    'push': 'PushNotification',
    'mobile': 'PushNotification',
    'webhook': 'WebhookExecution',

    # Other common issues
    'timeout': 'TimeoutError',
    'rate': 'RateLimitError',
    'throttle': 'RateLimitError',
    'api': 'APIError',
    'permission': 'PermissionError',
    'authentication': 'AuthenticationError',
    'auth': 'AuthenticationError'
}

def sample_message_patterns(user_query, database_name, timeout_seconds=5):
    """Sample the database to find actual message patterns related to the user's query.

    Uses caching for common patterns and has a fast timeout to avoid delays.
    """
    try:
        # Extract key terms from user query
        STOP_WORDS = {'why', 'is', 'my', 'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'how', 'what', 'when', 'where', 'who'}
        words = [w for w in user_query.lower().split() if len(w) > 2 and w.lower() not in STOP_WORDS]

        if not words:
            return None

        # Check cache first - instant lookup
        search_term = words[0].lower()
        if search_term in MESSAGE_PATTERN_CACHE:
            cached_pattern = MESSAGE_PATTERN_CACHE[search_term]
            logger.info(f"Using cached pattern for '{search_term}': {cached_pattern}")
            return cached_pattern

        # Check if any word matches cache
        for word in words:
            if word.lower() in MESSAGE_PATTERN_CACHE:
                cached_pattern = MESSAGE_PATTERN_CACHE[word.lower()]
                logger.info(f"Using cached pattern for '{word}': {cached_pattern}")
                return cached_pattern

        logger.info(f"No cached pattern found, sampling database for: {words}")

        # OPTIMIZED: Use recent partition and limit for speed
        sample_query = f"""
        select message
        from {database_name}.campaign_execution_v3
        where lower(message) like '%{search_term}%'
        and file_date >= date_add('day', -7, current_date)
        limit 3
        """

        # Add timeout wrapper using threading
        import threading
        result_container = [None]
        error_container = [None]

        def run_query():
            try:
                result_container[0] = query_athena(sample_query, database_name, f"Sample messages for {search_term}")
            except Exception as e:
                error_container[0] = e

        query_thread = threading.Thread(target=run_query)
        query_thread.daemon = True
        query_thread.start()
        query_thread.join(timeout=timeout_seconds)

        if query_thread.is_alive():
            logger.warning(f"Database sampling timed out after {timeout_seconds}s, using AI inference")
            return None

        if error_container[0]:
            logger.error(f"Database sampling error: {error_container[0]}")
            return None

        result = result_container[0]

        if result and result.get('data') and len(result['data']) > 0:
            # Extract patterns from actual messages
            messages = [row.get('message', '') for row in result['data']]
            logger.info(f"Found {len(messages)} sample messages containing '{search_term}'")

            # Return the most common distinct patterns (simplified - just return first match)
            if messages:
                # Look for key terms in the actual messages
                for msg in messages:
                    if search_term.lower() in msg.lower():
                        # Find the actual casing used in the message
                        import re
                        pattern = re.search(rf'\b\w*{search_term}\w*\b', msg, re.IGNORECASE)
                        if pattern:
                            actual_term = pattern.group(0)
                            logger.info(f"Found actual message pattern: {actual_term}")
                            # Cache the result for future queries
                            MESSAGE_PATTERN_CACHE[search_term] = actual_term
                            return actual_term

        logger.info(f"No message patterns found for '{search_term}', AI will guess")
        return None

    except Exception as e:
        logger.error(f"Error sampling message patterns: {e}")
        return None

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
        table_list = ', '.join(available_tables[:20]) if available_tables else "customer_campaign_logs.campaign_execution_v3"

        # Extract UUIDs from user query if provided
        import re
        uuid_pattern = r'[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}'
        found_uuids = re.findall(uuid_pattern, user_query)

        uuid_context = ""
        if found_uuids:
            uuid_context = f"\n\nUUIDs FOUND IN USER QUERY:\n"
            for uuid in found_uuids:
                uuid_context += f"- {uuid}\n"
            uuid_context += "Include these specific UUIDs in the query (use as user_uuid, campaign_uuid, or account_uuid based on context).\n"
            logger.info(f"Found UUIDs in query: {found_uuids}")

        # Sample the database to find actual message patterns (DISABLED for performance - use cache only)
        # actual_pattern = sample_message_patterns(user_query, database_name)

        # Use cache-only pattern matching for instant response
        actual_pattern = None
        STOP_WORDS = {'why', 'is', 'my', 'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'how', 'what', 'when', 'where', 'who'}
        words = [w for w in user_query.lower().split() if len(w) > 2 and w.lower() not in STOP_WORDS]

        # Check cache for instant pattern matching
        for word in words:
            if word in MESSAGE_PATTERN_CACHE:
                actual_pattern = MESSAGE_PATTERN_CACHE[word]
                logger.info(f"Using cached pattern for '{word}': {actual_pattern}")
                break

        pattern_context = ""
        if actual_pattern:
            pattern_context = f"\n\nKNOWN MESSAGE PATTERN:\nUse '{actual_pattern}' in your message like condition (verified pattern from common support queries).\n"
        else:
            logger.info("No cached pattern found, AI will infer from query")

        # --- Use the new call_gemini_api for analysis ---
        # FIX: Explicitly enforce the full table name in the template examples.
        analysis_prompt = f"""You are a Blueshift data analyst. Generate a relevant Athena SQL query for this support question: "{user_query}"{uuid_context}{pattern_context}

AVAILABLE DATA:
- Database: {database_name}
- Main table: customer_campaign_logs.campaign_execution_v3
- Key columns: timestamp, user_uuid, campaign_uuid, trigger_uuid, message, log_level, worker_name, transaction_uuid, execution_key

ANALYZE THE USER'S QUESTION and generate a query that matches actual Blueshift support query patterns.

IMPORTANT: Generate DETAILED, CONTEXTUAL queries based on the question type.

QUERY PATTERNS BY SCENARIO:

1. USER JOURNEY / "Why didn't user get message?" / Feature troubleshooting:
select timestamp, user_uuid, campaign_uuid, trigger_uuid, message, log_level, worker_name
from customer_campaign_logs.campaign_execution_v3
where account_uuid = 'client_account_uuid'
and user_uuid = 'client_user_uuid'
and campaign_uuid = 'client_campaign_uuid'
and file_date >= '2024-12-01'
and file_date < '2024-12-15'
order by timestamp asc
limit 500

WHY THIS QUERY: Shows complete user journey through campaign - all evaluations, checks, errors, successes in chronological order.
INCLUDES: trigger_uuid (which trigger fired), log_level (errors vs info), worker_name (which server processed)

2. ERROR INVESTIGATION / "Why are messages failing?":
select timestamp, user_uuid, campaign_uuid, trigger_uuid, message, log_level, execution_key
from customer_campaign_logs.campaign_execution_v3
where account_uuid = 'client_account_uuid'
and campaign_uuid = 'client_campaign_uuid'
and log_level = 'ERROR'
and file_date >= '2024-12-01'
and file_date < '2024-12-15'
order by timestamp desc
limit 200

WHY THIS QUERY: Focuses on errors only, recent first. execution_key links related log entries.

3. FEATURE SPECIFIC / "Quiet hours not working" / "Facebook sync issues":
select timestamp, user_uuid, campaign_uuid, trigger_uuid, message, log_level
from customer_campaign_logs.campaign_execution_v3
where account_uuid = 'client_account_uuid'
and campaign_uuid = 'client_campaign_uuid'
and message like '%{feature_pattern}%'
and file_date >= '2024-12-01'
and file_date < '2024-12-15'
order by timestamp asc
limit 300

WHY THIS QUERY: Filters for specific feature logs, shows chronological progression of feature behavior.

4. VOLUME ANALYSIS / "How many users affected?":
select
  file_date,
  count(distinct user_uuid) as affected_users,
  count(*) as total_occurrences,
  log_level
from customer_campaign_logs.campaign_execution_v3
where account_uuid = 'client_account_uuid'
and campaign_uuid = 'client_campaign_uuid'
and message like '%{error_pattern}%'
and file_date >= '2024-12-01'
and file_date < '2024-12-15'
group by file_date, log_level
order by file_date desc

WHY THIS QUERY: Shows impact over time - how many users hit the issue per day.

5. CAMPAIGN PERFORMANCE / "Is campaign sending?":
select
  log_level,
  count(*) as count,
  count(distinct user_uuid) as unique_users
from customer_campaign_logs.campaign_execution_v3
where account_uuid = 'client_account_uuid'
and campaign_uuid = 'client_campaign_uuid'
and file_date >= '2024-12-01'
and file_date < '2024-12-15'
group by log_level
order by count desc

WHY THIS QUERY: Summary view - how many ERRORs vs INFOs, overall campaign health.

ANALYZE THE USER'S QUESTION AND CHOOSE THE RIGHT PATTERN:
- Journey questions → Pattern 1 (detailed user journey)
- Error questions → Pattern 2 (error-focused)
- Feature questions → Pattern 3 (feature-specific)
- "How many" questions → Pattern 4 (volume analysis)
- "Is it working" questions → Pattern 5 (summary stats)

CRITICAL QUERY RULES:
1. **Include relevant columns** based on query type:
   - User journey: timestamp, user_uuid, campaign_uuid, trigger_uuid, message, log_level, worker_name
   - Error investigation: timestamp, user_uuid, campaign_uuid, trigger_uuid, message, log_level, execution_key
   - Volume analysis: Use COUNT, GROUP BY, aggregations
   - Feature-specific: timestamp, user_uuid, campaign_uuid, trigger_uuid, message, log_level

2. **Use appropriate WHERE conditions**:
   - Always: account_uuid, file_date range
   - Journey queries: Add user_uuid, campaign_uuid
   - Error queries: Add log_level = 'ERROR'
   - Feature queries: Add message like '%pattern%' (ONLY ONE)

3. **Set appropriate LIMIT**:
   - User journey: 500 (need full story)
   - Error investigation: 200 (enough to see patterns)
   - Feature-specific: 300 (see progression)
   - Volume analysis: No limit needed (aggregated)

4. **Use correct ORDER BY**:
   - Journey queries: timestamp ASC (chronological story)
   - Error queries: timestamp DESC (recent errors first)
   - Volume queries: date DESC or count DESC

5. **Date ranges**:
   - Use: file_date >= '2024-12-01' AND file_date < '2024-12-15'
   - This ensures partition pruning for performance

EXAMPLE OUTPUTS:

For "Why didn't user 123 receive message?":
```sql
select timestamp, user_uuid, campaign_uuid, trigger_uuid, message, log_level, worker_name
from customer_campaign_logs.campaign_execution_v3
where account_uuid = 'client_account_uuid'
and user_uuid = 'client_user_uuid'
and campaign_uuid = 'client_campaign_uuid'
and file_date >= '2024-12-01'
and file_date < '2024-12-15'
order by timestamp asc
limit 500
```

For "How many users are getting channel limit errors?":
```sql
select
  file_date,
  count(distinct user_uuid) as affected_users,
  count(*) as total_occurrences
from customer_campaign_logs.campaign_execution_v3
where account_uuid = 'client_account_uuid'
and campaign_uuid = 'client_campaign_uuid'
and message like '%ChannelLimitError%'
and file_date >= '2024-12-01'
and file_date < '2024-12-15'
group by file_date
order by file_date desc
```

For "Are there Facebook sync errors?":
```sql
select timestamp, user_uuid, campaign_uuid, trigger_uuid, message, log_level
from customer_campaign_logs.campaign_execution_v3
where account_uuid = 'client_account_uuid'
and campaign_uuid = 'client_campaign_uuid'
and log_level = 'ERROR'
and message like '%FacebookAudienceSync%'
and file_date >= '2024-12-01'
and file_date < '2024-12-15'
order by timestamp desc
limit 200
```

Generate a query specifically for: "{user_query}"

Format your response as:
DATABASE: {database_name}

SQL_QUERY:
[Write the exact format shown above - each clause on its own line with proper spacing]

INSIGHT_EXPLANATION:
[Brief explanation of what this query searches for and why it helps with the user's question]"""

        # Call the unified Gemini API function (temperature 0.0 for deterministic SQL generation)
        ai_response = call_gemini_api(query=analysis_prompt, platform_resources=None, temperature=0.0)
        # --- End Gemini API call ---

        if ai_response.startswith("Error:"):
            logger.error(f"Athena AI API error: {ai_response}")
            return get_default_athena_insights(user_query)

        logger.info(f"Athena AI response: {ai_response[:200]}...")
        return parse_athena_analysis(ai_response, user_query)

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

        # Validate and refine the query before returning
        if sql_query.strip():
            # Ensure we use placeholder values instead of real data
            safe_sql_query = customize_query_for_execution(sql_query.strip(), user_query)

            # TEST QUERY: Validate it works and return sample results
            validated_query = validate_and_test_query(safe_sql_query, database_name, user_query, explanation.strip())

            if validated_query:
                return validated_query
            else:
                # If validation fails, return original query
                print(f"Generated SQL Query: {safe_sql_query}")  # Debug output
                return {
                    'database': database_name,
                    'sql_query': safe_sql_query,
                    'explanation': explanation.strip() + "\n\n💡 Copy this query to AWS Athena console and customize with specific account_uuid, campaign_uuid, and date ranges for your support case.",
                    'results': {"note": "Query template ready for manual customization in Athena", "data": []},
                    'has_data': False
                }
        else:
            return get_default_athena_insights(user_query)

    except Exception as e:
        print(f"Error parsing Athena analysis: {e}")
        return get_default_athena_insights(user_query)

def validate_and_test_query(sql_query, database_name, user_query, explanation):
    """Validate the query by testing it with a small sample and return refined version with actual results"""
    try:
        logger.info("Validating and testing generated Athena query...")

        # Create a test version of the query - remove placeholders and add realistic test conditions
        test_query = sql_query.replace("'client_account_uuid'", "(select account_uuid from customer_campaign_logs.campaign_execution_v3 limit 1)")
        test_query = test_query.replace("'client_campaign_uuid'", "(select campaign_uuid from customer_campaign_logs.campaign_execution_v3 where campaign_uuid is not null limit 1)")
        test_query = test_query.replace("'client_user_uuid'", "(select user_uuid from customer_campaign_logs.campaign_execution_v3 where user_uuid is not null limit 1)")

        # Ensure it's limited to prevent long queries
        if 'limit' not in test_query.lower():
            test_query += "\nlimit 10"
        else:
            # Replace any large limits with 10 for testing
            import re
            test_query = re.sub(r'limit\s+\d+', 'limit 10', test_query, flags=re.IGNORECASE)

        logger.info(f"Testing query: {test_query[:200]}...")

        # Execute the test query with short timeout
        result = query_athena(test_query, database_name, f"Validate query for: {user_query}")

        if result and not result.get('error'):
            # Query works! Get the actual structure
            sample_data = result.get('data', [])
            columns = result.get('columns', [])

            logger.info(f"✅ Query validated successfully! Found {len(sample_data)} sample rows")

            # Build enhanced explanation with sample results
            enhanced_explanation = explanation + "\n\n"

            if sample_data and len(sample_data) > 0:
                enhanced_explanation += f"✅ **Query Validated**: This query successfully returns results from your database.\n\n"
                enhanced_explanation += f"**Sample Results Preview** ({len(sample_data)} rows):\n"
                for i, row in enumerate(sample_data[:3], 1):
                    enhanced_explanation += f"\n{i}. "
                    # Show first few columns
                    for col in columns[:3]:
                        value = row.get(col, 'N/A')
                        enhanced_explanation += f"{col}: {str(value)[:50]}... | "
                    enhanced_explanation = enhanced_explanation.rstrip(' | ')
            else:
                enhanced_explanation += "⚠️ **Query is valid but returned no results**. This might mean:\n"
                enhanced_explanation += "- The message pattern doesn't exist in recent logs\n"
                enhanced_explanation += "- The date range needs adjustment\n"
                enhanced_explanation += "- Try a broader search term\n"

            enhanced_explanation += "\n\n💡 **Next Steps**: Copy this query and replace placeholders with your specific account_uuid, campaign_uuid, user_uuid, and date range."

            return {
                'database': database_name,
                'sql_query': sql_query,  # Return original with placeholders
                'explanation': enhanced_explanation,
                'results': {"data": sample_data[:5], "columns": columns, "note": "Sample results from validation query"},
                'has_data': True if sample_data else False
            }
        else:
            # Query failed
            error_msg = result.get('error', 'Unknown error')
            logger.warning(f"❌ Query validation failed: {error_msg}")

            # Return query with error context
            return {
                'database': database_name,
                'sql_query': sql_query,
                'explanation': explanation + f"\n\n⚠️ **Validation Note**: Initial test of this query encountered an issue ({error_msg}). You may need to adjust the query parameters for your specific use case.",
                'results': {"note": "Query validation failed - may need adjustments", "data": []},
                'has_data': False
            }

    except Exception as e:
        logger.error(f"Error validating query: {e}")
        return None  # Fall back to original behavior

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
        # Use send_file to correctly serve the file
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

        # DEBUG: Log the query
        logger.info(f"Processing query: {query}")

        # --- UPDATE FUNCTION CALL ---
        # Call improved resource generation function
        related_resources = generate_related_resources_improved(query)
        # --- END UPDATE ---

        # DEBUG: Log what content was actually retrieved
        platform_resources_with_content = related_resources.get('platform_resources_with_content', [])
        logger.info(f"Retrieved {len(platform_resources_with_content)} resources with content")

        # NEW: Check if any content actually contains step instructions
        total_step_content = 0
        for resource in platform_resources_with_content:
            content = resource.get('content', '')
            step_indicators = ['step', 'navigate', 'click', 'select', 'go to']
            has_steps = any(indicator in content.lower() for indicator in step_indicators)
            if has_steps:
                total_step_content += 1
            logger.info(f"- {resource['title']}: {len(content)} chars, has_steps: {has_steps}")

        logger.info(f"Resources with actual step content: {total_step_content}")

        # Call the new Gemini API function
        ai_response = call_gemini_api(query, platform_resources_with_content)

        # Generate Athena insights
        athena_insights = generate_athena_insights(query)

        # Generate suggested follow-up questions
        suggested_followups = generate_followup_suggestions(query, ai_response)

        return jsonify({
            "response": ai_response,
            "resources": related_resources,
            "athena_insights": athena_insights,
            "suggested_followups": suggested_followups
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

        # Call Gemini API
        ai_response = call_gemini_api(followup_query)

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
            line-height: 1.8;
            color: #555555 !important;
            font-weight: 400;
            font-size: 1.05em;
        }

        .response-content strong {
            font-weight: 700;
            color: #2c3e50;
        }

        .response-content h3 {
            color: #2790FF;
            margin-top: 20px;
            margin-bottom: 10px;
            font-size: 1.3em;
        }

        .response-content h4 {
            color: #2790FF;
            margin-top: 15px;
            margin-bottom: 8px;
            font-size: 1.1em;
        }

        /* INTERACTIVE FOLLOW-UP SECTION */
        .followup-section {
            background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);
            border-radius: 15px;
            padding: 25px;
            margin: 25px 0;
            border-left: 5px solid #2790FF;
            display: none;
        }

        .followup-section h4 {
            color: #2790FF;
            margin-top: 0;
            font-size: 1.2em;
            margin-bottom: 10px;
        }

        .followup-section p.subtitle {
            margin: 0 0 15px 0;
            color: #666;
            font-size: 0.9rem;
        }

        .followup-suggestions {
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            margin-top: 15px;
        }

        .followup-chip {
            background: white;
            border: 2px solid #2790FF;
            color: #2790FF;
            padding: 12px 20px;
            border-radius: 25px;
            font-size: 0.9rem;
            cursor: pointer;
            transition: all 0.3s ease;
            font-family: 'Calibri', sans-serif;
            font-weight: 500;
            display: inline-flex;
            align-items: center;
            gap: 8px;
        }

        .followup-chip:hover {
            background: #2790FF;
            color: white;
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(39, 144, 255, 0.3);
        }

        .followup-chip:active {
            transform: translateY(0);
        }

        .followup-chip::before {
            content: "→";
            font-weight: bold;
            font-size: 1.1em;
        }

        /* Deprecated old input style */
        .followup-container {
            display: none;
        }

        #followupInput {
            display: none;
        }

        #followupBtn {
            display: none;
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

            <div class="followup-section" id="followupSection">
                <h4>Have a follow-up question?</h4>
                <p class="subtitle">Click on a suggested question to continue exploring this topic:</p>
                <div class="followup-suggestions" id="followupSuggestions">
                    <!-- Follow-up suggestions will be dynamically inserted here -->
                </div>
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
        function formatResponse(text) {
            if (!text) return '';
            var div = document.createElement('div');
            var lines = text.split('\n');

            lines.forEach(function(line) {
                if (line.startsWith('## ')) {
                    var h3 = document.createElement('h3');
                    h3.style.color = '#2790FF';
                    h3.style.marginTop = '20px';
                    h3.style.marginBottom = '10px';
                    h3.textContent = line.substring(3);
                    div.appendChild(h3);
                } else if (line.startsWith('### ')) {
                    var h4 = document.createElement('h4');
                    h4.style.color = '#2790FF';
                    h4.style.marginTop = '15px';
                    h4.style.marginBottom = '8px';
                    h4.textContent = line.substring(4);
                    div.appendChild(h4);
                } else {
                    var p = document.createElement('p');
                    p.style.margin = '5px 0';
                    var parts = line.split('**');
                    parts.forEach(function(part, idx) {
                        if (idx % 2 === 1) {
                            var strong = document.createElement('strong');
                            strong.textContent = part;
                            p.appendChild(strong);
                        } else {
                            p.appendChild(document.createTextNode(part));
                        }
                    });
                    div.appendChild(p);
                }
            });

            return div.innerHTML;
        }

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

                // Show response with formatting
                document.getElementById('responseContent').innerHTML = formatResponse(data.response);
                const resultsContainer = document.getElementById('resultsContainer');
                resultsContainer.style.display = 'block';
                resultsContainer.classList.add('show');

                // Show resources in 4-column grid
                showResources(data.resources);

                // Show Athena insights if available
                if (data.athena_insights) {
                    showAthenaInsights(data.athena_insights);
                }

                // Show follow-up suggestions if available
                if (data.suggested_followups && data.suggested_followups.length > 0) {
                    showFollowupSuggestions(data.suggested_followups);
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

        function showFollowupSuggestions(suggestions) {
            const followupSection = document.getElementById('followupSection');
            const followupSuggestions = document.getElementById('followupSuggestions');

            // Clear any existing suggestions
            followupSuggestions.innerHTML = '';

            // Create clickable chips for each suggestion
            suggestions.forEach(suggestion => {
                const chip = document.createElement('button');
                chip.className = 'followup-chip';
                chip.textContent = suggestion;
                chip.onclick = function() {
                    // When clicked, trigger a new query with this suggestion
                    document.getElementById('queryInput').value = suggestion;
                    document.getElementById('searchBtn').click();

                    // Scroll to top to see the new results
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                };
                followupSuggestions.appendChild(chip);
            });

            // Show the follow-up section
            followupSection.style.display = 'block';
        }

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

        // Removed old followup input event listener - now using interactive chips

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
    print(f"JIRA_TOKEN: {'SET' if JIRA_TOKEN else 'NOT SET'}")
    print(f"JIRA_EMAIL: {'SET' if JIRA_EMAIL else 'NOT SET'}")
    print(f"CONFLUENCE_TOKEN: {'SET' if CONFLUENCE_TOKEN else 'NOT SET'}")
    print(f"CONFLUENCE_EMAIL: {'SET' if CONFLUENCE_EMAIL else 'NOT SET'}")
    print(f"ZENDESK_TOKEN: {'SET' if ZENDESK_TOKEN else 'NOT SET'}")
    print(f"ZENDESK_EMAIL: {'SET' if ZENDESK_EMAIL else 'NOT SET'}")
    print(f"ZENDESK_SUBDOMAIN: {'SET' if ZENDESK_SUBDOMAIN else 'NOT SET'}")
    print("=" * 40)
    
    # Print API status results
    print("\n=== External API Status ===")
    for api, status in API_STATUS.items():
        print(f"{api.upper()}: {'✅ Connected' if status else '❌ Failed/Missing Credentials'}")
    print("=" * 40)

    # ADDED Diagnostic Print Statement
    print("--- ATTEMPTING TO START FLASK APP ---")

    app.run(host='0.0.0.0', port=port, debug=True)
