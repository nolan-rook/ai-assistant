import requests
import logging
from datetime import datetime, timedelta

def fetch_ai_news():
    api_key = '87ba80fbe1284532bbf8b284e78cea7f'  # Replace with your News API key
    url = "https://newsapi.org/v2/everything"
    query = "artificial intelligence"
    from_date = (datetime.now() - timedelta(days=1.1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    
    params = {
        'q': query,
        'from': from_date,
        'sortBy': 'publishedAt',
        'apiKey': api_key
    }
    
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        articles = response.json().get('articles', [])
        news_items = []
        for article in articles:
            title = article.get('title')
            summary = article.get('description')
            link = article.get('url')
            news_items.append({'title': title, 'summary': summary, 'url': link})
        return news_items
    except requests.HTTPError as http_err:
        logging.error(f"HTTP error occurred while fetching AI news: {http_err}")
        return []
    except Exception as e:
        logging.error(f"An error occurred while fetching AI news: {e}")
        return []

# Example usage
if __name__ == "__main__":
    news = fetch_ai_news()
    for item in news:
        print(f"Title: {item['title']}")
        print(f"Summary: {item['summary']}")
        print(f"URL: {item['url']}\n")