import json
import yaml

input_json = r"C:\Users\user\Desktop\technoplus-service\public\data\services.json"
output_yaml = r"C:\Users\user\Desktop\ChatBOT\knowledge_template.yaml"

def generate_yaml():
    with open(input_json, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    template = {
        "global_faq": [
            {
                "question": "Скільки часу займає діагностика та ремонт?",
                "answer": "Стандартна діагностика зазвичай займає від 1 до 3 робочих днів. Більшість ремонтів ми виконуємо за 1-2 дні після погодження вартості."
            },
            {
                "question": "Скільки коштує діагностика, якщо я відмовлюсь від ремонту?",
                "answer": "Діагностика безкоштовна за умови подальшого ремонту в нашому сервісному центрі. У разі відмови від ремонту оплачується лише базова вартість діагностики."
            },
            {
                "question": "Чи надаєте ви гарантію на виконані роботи?",
                "answer": "Так, ми надаємо гарантію на всі види ремонтних робіт та встановлені запчастини. Термін гарантії залежить від типу послуги та деталі."
            }
        ],
        "categories": []
    }
    
    # These FAQs are from FaqPage.tsx and ServicePage.tsx that we found earlier
    hardcoded_faqs = {
        "speakers": [
            {"question": "Що робити, якщо колонка не заряджається або швидко сідає?", "answer": "Найчастіше причина у зношеному акумуляторі, роз'ємі зарядки або платі BMS."}
        ],
        "phones": [
            {"question": "Чи ремонтуєте телефони після води?", "answer": "Так. Проводимо чистку плати, відновлення ланцюгів живлення та заміну пошкоджених елементів."}
        ],
        "tvs": [
            {"question": "Телевізор має звук, але немає зображення. Це ремонтується?", "answer": "Так, часто причина у підсвітці, платі живлення або матриці."}
        ],
        "computers": [
            {"question": "Ноутбук гріється і шумить. Що потрібно робити?", "answer": "Потрібна чистка системи охолодження, заміна термоінтерфейсів і перевірка вентилятора."}
        ]
    }
    
    for cat in data.get("categories", []):
        cat_data = {
            "slug": cat.get("id"),
            "title": cat.get("title"),
            "description": cat.get("description"),
            "detailed_description": cat.get("detailedDescription"),
            "services": [
                {"name": s["name"], "price": s["price"]} for s in cat.get("services", [])
            ],
            "problems": cat.get("problems", []),
            "faqs": hardcoded_faqs.get(cat.get("id"), [])
        }
        template["categories"].append(cat_data)
        
    with open(output_yaml, "w", encoding="utf-8") as f:
        yaml.dump(template, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
        
    print(f"✅ Успішно згенеровано {output_yaml}")

if __name__ == "__main__":
    generate_yaml()
