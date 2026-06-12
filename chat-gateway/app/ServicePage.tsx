import React, { useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, CheckCircle, AlertCircle, ChevronRight, Wrench } from 'lucide-react';
import { FaMapMarkerAlt, FaPhone } from 'react-icons/fa';
import SEO from '../components/SEO';
import { withBasePath } from '@/lib/utils';
import ParallaxSection from '@/components/ParallaxSection';
import Reveal from '@/components/Reveal';

interface ServicePageProps {
    categories: any[];
    setHeaderBottomContent: (content: React.ReactNode) => void;
}

interface FaqItem {
    question: string;
    answer: string;
}

const slugifyBrand = (name: string) =>
    name
        .toLowerCase()
        .replace(/&/g, 'and')
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '');

const serviceKeywordByCategory: Record<string, string> = {
    speakers: 'колонок',
    phones: 'телефонів',
    tvs: 'телевізорів',
    appliances: 'побутової техніки',
    computers: 'ноутбуків і компʼютерів',
    headphones: 'навушників',
    'power-stations': 'зарядних станцій',
    'coffee-machines': 'кавомашин',
    'pc-assembly': 'компʼютерів',
};

const photoLikeLogoFiles = new Set([
    '/images/brands/apple_5.webp',
    '/images/brands/apple_6.webp',
    '/images/brands/apple_7.webp',
    '/images/brands/anker.webp',
    '/images/brands/asus.webp',
    '/images/brands/hp.webp',
    '/images/brands/jackery.webp',
    '/images/brands/samsung.webp',
    '/images/brands/sony.webp',
    '/images/brands/xiaomi.webp',
]);

const faqByCategory: Record<string, FaqItem[]> = {
    speakers: [
        {
            question: 'Чи ремонтуєте портативні колонки JBL, Sony, Marshall та Harman Kardon?',
            answer: 'Так, працюємо з популярними портативними колонками цих брендів: відновлюємо живлення, зарядку, Bluetooth, кнопки, розʼєми, акумулятори, динаміки та корпусні вузли.',
        },
        {
            question: 'Що робити, якщо колонка не заряджається або швидко сідає?',
            answer: 'Найчастіше причина у зношеному акумуляторі, розʼємі зарядки, контролері живлення або платі BMS. Після діагностики називаємо точну причину і погоджуємо вартість.',
        },
        {
            question: 'Чи можна відновити колонку після води?',
            answer: 'Так, але результат залежить від часу після залиття та стану плати. Бажано не заряджати пристрій і принести його на діагностику якнайшвидше.',
        },
        {
            question: 'Чи зберігається захист від вологи після ремонту?',
            answer: 'Після розбирання заводська герметичність може змінитися. Ми акуратно відновлюємо ущільнення там, де це конструктивно можливо, але чесно попереджаємо про обмеження.',
        },
    ],
    phones: [
        {
            question: 'Скільки часу займає ремонт телефону у Львові?',
            answer: 'Типові роботи на кшталт заміни дисплея, батареї або розʼєму часто виконуються в день звернення за наявності запчастин. Складний компонентний ремонт потребує більше часу.',
        },
        {
            question: 'Чи ремонтуєте телефони після води?',
            answer: 'Так. Проводимо чистку плати, відновлення ланцюгів живлення та заміну пошкоджених елементів. Важливо не вмикати і не заряджати телефон після потрапляння рідини.',
        },
        {
            question: 'Чи збережуться дані після ремонту смартфона?',
            answer: 'У більшості ремонтів дані не чіпаємо. Якщо є ризик для памʼяті або потрібне відновлення даних, попереджаємо до початку робіт.',
        },
        {
            question: 'Які запчастини використовуєте для Apple, Samsung і Xiaomi?',
            answer: 'Підбираємо оригінальні, сервісні або якісні сумісні деталі залежно від моделі, бюджету і доступності. Варіанти та ціну погоджуємо до ремонту.',
        },
    ],
    tvs: [
        {
            question: 'Телевізор має звук, але немає зображення. Це ремонтується?',
            answer: 'Так, часто причина у підсвітці, платі живлення або матриці. Після діагностики пояснюємо, чи ремонт економічно доцільний.',
        },
        {
            question: 'Чи ремонтуєте Smart TV, якщо не працює Wi-Fi або додатки?',
            answer: 'Так, перевіряємо модуль Wi-Fi, програмне забезпечення, памʼять і основну плату. За потреби оновлюємо або відновлюємо прошивку.',
        },
        {
            question: 'Чи можна замінити розбиту матрицю телевізора?',
            answer: 'Технічно так, але часто матриця коштує майже як новий телевізор. Ми одразу підкажемо, чи є сенс у такому ремонті.',
        },
        {
            question: 'Чи приймаєте великі телевізори у сервісному центрі?',
            answer: 'Так, приймаємо телевізори різних діагоналей. Перед візитом бажано зателефонувати, щоб уточнити зручний час і деталі транспортування.',
        },
    ],
    appliances: [
        {
            question: 'Чи ремонтуєте дрібну побутову техніку, якщо вона не вмикається?',
            answer: 'Так, перевіряємо кабель, кнопки, термозахист, двигун, плату керування та силові елементи. Після діагностики називаємо реальну вартість ремонту.',
        },
        {
            question: 'Чи є сенс ремонтувати кухонний комбайн або блендер?',
            answer: 'Зазвичай так, якщо корпус і основні механічні вузли в доброму стані. Ми окремо оцінюємо вартість запчастин і порівнюємо її з ціною нового пристрою.',
        },
        {
            question: 'Чи ремонтуєте мікрохвильові печі, які не гріють?',
            answer: 'Так, перевіряємо високовольтний запобіжник, магнетрон, трансформатор, діоди, конденсатор і плату керування. Такі роботи виконуємо з дотриманням безпеки.',
        },
        {
            question: 'Чи можна замовити деталь, якщо її немає в наявності?',
            answer: 'Так, підбираємо сумісну або оригінальну запчастину під конкретну модель і погоджуємо терміни доставки перед замовленням.',
        },
    ],
    computers: [
        {
            question: 'Чи ремонтуєте ноутбуки Apple, Asus, Lenovo, HP, Dell та Acer?',
            answer: 'Так, працюємо з цими та іншими брендами: ремонтуємо живлення, охолодження, екрани, клавіатури, корпуси, плати, памʼять, накопичувачі та програмні збої.',
        },
        {
            question: 'Ноутбук гріється і шумить. Що потрібно робити?',
            answer: 'Потрібна чистка системи охолодження, заміна термоінтерфейсів і перевірка вентилятора. Якщо перегрів спричинив пошкодження плати, це покаже діагностика.',
        },
        {
            question: 'Чи можна прискорити старий ноутбук або компʼютер?',
            answer: 'Так, часто допомагає SSD, збільшення оперативної памʼяті, чиста система та правильне налаштування автозавантаження. Підбираємо апгрейд під ваші задачі.',
        },
        {
            question: 'Чи збережуться файли під час ремонту?',
            answer: 'За можливості не втручаємося в дані. Якщо ремонт повʼязаний із накопичувачем або системою, попереджаємо про ризики та пропонуємо резервне копіювання.',
        },
    ],
    headphones: [
        {
            question: 'Чи всі TWS навушники можна відремонтувати?',
            answer: 'Не всі. Частина TWS моделей нерозбірна або нерентабельна в ремонті, але ми можемо перевірити акумулятор, контакти, сітки, кейс, зарядку і сказати чесно.',
        },
        {
            question: 'Чи відновлюєте дорогі повнорозмірні навушники?',
            answer: 'Так. Для преміальних класичних моделей ремонтуємо кабелі, штекери, динаміки, дуги, кронштейни, амбушури та реставруємо корпуси після тріщин або падінь.',
        },
        {
            question: 'Один навушник грає тихіше. Це можна виправити?',
            answer: 'Так, причина може бути в забрудненій сітці, акумуляторі, шлейфі, динаміку або електроніці. Починаємо з діагностики, щоб не міняти зайвого.',
        },
        {
            question: 'Чи ремонтуєте кейси AirPods, Samsung Galaxy Buds та інших TWS?',
            answer: 'Так, перевіряємо акумулятор кейса, розʼєм зарядки, контакти, плату живлення та індикацію. Якщо ремонт недоцільний, попереджаємо до замовлення деталей.',
        },
    ],
    'power-stations': [
        {
            question: 'Чи ремонтуєте зарядні станції EcoFlow, Bluetti, Anker та Jackery?',
            answer: 'Так, проводимо діагностику зарядних станцій, перевіряємо інвертор, BMS, батарейний блок, розʼєми, плату зарядки, дисплей і програмні помилки.',
        },
        {
            question: 'Станція не видає 220 В. Що може бути причиною?',
            answer: 'Можлива несправність інвертора, силових ключів, BMS, реле, плати керування або захисту. Без діагностики точно визначити причину неможливо.',
        },
        {
            question: 'Чи можна замінити батарейні елементи в зарядній станції?',
            answer: 'У частині моделей так, але важлива сумісність елементів і коректна робота BMS. Ми оцінюємо безпеку та доцільність такого ремонту окремо.',
        },
        {
            question: 'Чи ремонтуєте станції після перевантаження або стрибка напруги?',
            answer: 'Так, перевіряємо силову частину, інвертор, захисні елементи та плату керування. Після цього погоджуємо варіанти ремонту.',
        },
    ],
    'coffee-machines': [
        {
            question: 'Чи ремонтуєте кавомашини DeLonghi, Philips, Saeco, Jura та Krups?',
            answer: 'Так, працюємо з популярними домашніми кавомашинами: усуваємо протікання, помилки, проблеми з помпою, бойлером, кавомолкою, заварним блоком і датчиками.',
        },
        {
            question: 'Кавомашина не подає воду або погано готує каву. Що робити?',
            answer: 'Причина може бути в накипі, помпі, клапанах, флоуметрі, бойлері або заварному блоці. Проводимо діагностику і чистку вузлів за потреби.',
        },
        {
            question: 'Чи потрібна профілактика кавомашини?',
            answer: 'Так, регулярна чистка від кавових масел і накипу продовжує ресурс помпи, бойлера та заварного механізму. Особливо це важливо при жорсткій воді.',
        },
        {
            question: 'Чи ремонтуєте кавомолку в автоматичній кавомашині?',
            answer: 'Так, перевіряємо жорна, двигун, редуктор, датчики та налаштування помелу. Після ремонту тестуємо стабільність порції кави.',
        },
    ],
    'pc-assembly': [
        {
            question: 'Чи допомагаєте підібрати комплектуючі для нового ПК?',
            answer: 'Так, підбираємо процесор, відеокарту, плату, памʼять, накопичувач, блок живлення та корпус під задачі, бюджет і майбутній апгрейд.',
        },
        {
            question: 'Чи можна модернізувати старий компʼютер без повної заміни?',
            answer: 'Так, часто достатньо оновити SSD, оперативну памʼять, відеокарту або блок живлення. Перед апгрейдом перевіряємо сумісність і доцільність.',
        },
        {
            question: 'Чи тестуєте компʼютер після збірки?',
            answer: 'Так, перевіряємо температури, стабільність, памʼять, накопичувачі, драйвери та базові сценарії навантаження, щоб система працювала без збоїв.',
        },
        {
            question: 'Чи налаштовуєте Windows, драйвери та BIOS?',
            answer: 'Так, встановлюємо систему, драйвери, оновлення, налаштовуємо BIOS/UEFI, профілі памʼяті, вентилятори і базову безпеку.',
        },
    ],
};

const ServicePage: React.FC<ServicePageProps> = ({ categories, setHeaderBottomContent }) => {
    const { id, brandSlug } = useParams<{ id: string; brandSlug?: string }>();
    const category = categories.find(c => c.id === id);
    const shouldUseTextLogo = (logo: string) => !logo || /^(https?:)?\/\//.test(logo) || photoLikeLogoFiles.has(logo);
    const selectedBrand = category?.brands?.find((brand: any) => slugifyBrand(brand.name) === brandSlug);
    const serviceKeyword = category ? serviceKeywordByCategory[category.id] || 'техніки' : 'техніки';
    const faqs = category ? faqByCategory[category.id] || [] : [];

    useEffect(() => {
        window.scrollTo(0, 0);
    }, [id, brandSlug]);

    useEffect(() => {
        if (!category) return;
        
        setHeaderBottomContent(
            <div className="w-full border-b border-neutral-100 shadow-sm">
                <div className="container mx-auto px-4 py-2 flex items-center justify-between gap-3">
                    <Link
                        to="/#services"
                        className="inline-flex items-center gap-2 text-sm font-semibold text-neutral-700 hover:text-primary-600 transition-colors min-w-0"
                    >
                        <ArrowLeft className="w-4 h-4 flex-shrink-0" />
                        <span className="truncate">Назад до всіх послуг</span>
                    </Link>

                    <div className="hidden sm:flex items-center gap-2 text-sm text-neutral-600 min-w-0">
                        <Link to="/" className="hover:text-primary-600 transition-colors truncate">
                            Головна
                        </Link>
                        <ChevronRight className="w-4 h-4 text-neutral-400 flex-shrink-0" />
                        <Link to="/#services" className="hover:text-primary-600 transition-colors truncate">
                            Послуги
                        </Link>
                        <ChevronRight className="w-4 h-4 text-neutral-400 flex-shrink-0" />
                        <span className="font-semibold text-neutral-800 truncate">
                            {selectedBrand ? `${category.title}: ${selectedBrand.name}` : category.title}
                        </span>
                    </div>
                </div>
            </div>
        );

        return () => setHeaderBottomContent(null);
    }, [category, selectedBrand, setHeaderBottomContent]);

    if (!category) {
        return (
            <div className="min-h-screen flex items-center justify-center">
                <div className="text-center">
                    <h2 className="text-2xl font-bold text-neutral-800 mb-4">Послугу не знайдено</h2>
                    <Link to="/" className="text-primary-600 hover:text-primary-700 font-medium">
                        Повернутися на головну
                    </Link>
                </div>
            </div>
        );
    }

    if (brandSlug && !selectedBrand) {
        return (
            <div className="min-h-screen flex items-center justify-center">
                <div className="text-center">
                    <h2 className="text-2xl font-bold text-neutral-800 mb-4">Бренд для цієї послуги не знайдено</h2>
                    <Link to={`/service/${id}`} className="text-primary-600 hover:text-primary-700 font-medium">
                        Повернутися до послуги
                    </Link>
                </div>
            </div>
        );
    }

    const canonicalUrl = selectedBrand
        ? `https://texno.plus/service/${id}/${brandSlug}`
        : `https://texno.plus/service/${id}`;
    const imageUrl = `https://texno.plus${category.image}`;
    const seoTitle = selectedBrand
        ? `Ремонт ${serviceKeyword} ${selectedBrand.name} у Львові | Сервісний центр Техноплюс`
        : (category.metaTitle || `${category.title} | Сервісний центр Техноплюс`);
    const seoDescription = selectedBrand
        ? `Професійний ремонт ${serviceKeyword} ${selectedBrand.name} у Львові. Адреса: вул. І. Огієнка, 15. Тел: +38 (067) 385 15 60.`
        : `${category.metaDescription || category.description} Адреса: вул. І. Огієнка, 15. Тел: +38 (067) 385 15 60.`;
    const pageHeading = selectedBrand
        ? `Ремонт ${serviceKeyword} ${selectedBrand.name} у Львові`
        : category.title;

    const breadcrumbJsonLd = JSON.stringify({
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": selectedBrand ? [{
            "@type": "ListItem",
            "position": 1,
            "name": "Головна",
            "item": "https://texno.plus"
        }, {
            "@type": "ListItem",
            "position": 2,
            "name": category.title,
            "item": `https://texno.plus/service/${id}`
        }, {
            "@type": "ListItem",
            "position": 3,
            "name": selectedBrand.name,
            "item": canonicalUrl
        }] : [{
            "@type": "ListItem",
            "position": 1,
            "name": "Головна",
            "item": "https://texno.plus"
        }, {
            "@type": "ListItem",
            "position": 2,
            "name": category.title,
            "item": canonicalUrl
        }]
    });

    const getLowPrice = (services: any[]) => {
        const prices = services
            .map((service) => service.price.match(/\d+/)?.[0])
            .filter(Boolean)
            .map(Number);

        return prices.length > 0 ? Math.min(...prices) : 200;
    };

    const serviceJsonLd = JSON.stringify({
        "@context": "https://schema.org",
        "@type": "Service",
        "name": pageHeading,
        "image": "https://texno.plus" + category.image,
        "description": seoDescription,
        "provider": {
            "@type": "LocalBusiness",
            "name": "Сервісний центр \"Техноплюс\"",
            "telephone": "+380673851560",
            "address": {
                "@type": "PostalAddress",
                "streetAddress": "вул. Івана Огієнка, 15",
                "addressLocality": "Львів",
                "addressCountry": "UA"
            }
        },
        "areaServed": {
            "@type": "City",
            "name": "Львів"
        },
        "brand": {
            "@type": "Brand",
            "name": selectedBrand?.name || "TechnoPlus"
        },
        "offers": {
            "@type": "AggregateOffer",
            "priceCurrency": "UAH",
            "lowPrice": getLowPrice(category.services),
            "offerCount": category.services.length
        }
    });
    const faqJsonLd = JSON.stringify({
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": faqs.map((faq) => ({
            "@type": "Question",
            "name": faq.question,
            "acceptedAnswer": {
                "@type": "Answer",
                "text": faq.answer
            }
        }))
    });

    return (
        <>
            <SEO
                title={seoTitle}
                description={seoDescription}
                keywords={category.keywords}
                canonicalUrl={canonicalUrl}
                imageUrl={imageUrl}
                jsonLd={faqs.length > 0 ? `[${breadcrumbJsonLd}, ${serviceJsonLd}, ${faqJsonLd}]` : `[${breadcrumbJsonLd}, ${serviceJsonLd}]`}
            />

            <div className="pt-[var(--header-height)] pb-16 min-h-screen">
                <div className="container mx-auto px-4 pt-6">

                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-12 mb-16">
                        <Reveal>
                            <ParallaxSection
                                backgroundImage={category.image}
                                className="relative rounded-2xl overflow-hidden shadow-2xl min-h-[300px] lg:aspect-square"
                            />
                        </Reveal>

                        <div className="space-y-8">
                            <Reveal delayMs={120}>
                                <div className="bg-white rounded-2xl p-8 shadow-lg border border-neutral-100">
                                    <div className="flex items-center gap-3 mb-4">
                                        <div className="text-primary-500 text-3xl">{category.icon}</div>
                                        <div className="text-neutral-500 text-sm font-medium uppercase tracking-wider">
                                            Послуги / {category.title}
                                        </div>
                                    </div>
                                    <h1 className="text-3xl lg:text-4xl font-bold text-neutral-800 mb-6 leading-tight">
                                        {pageHeading}
                                    </h1>
                                    <div className="flex flex-col sm:flex-row gap-4 sm:gap-6 text-sm text-neutral-600 mb-8 pb-6 border-b border-neutral-100">
                                        <div className="flex items-center gap-2">
                                            <FaMapMarkerAlt className="text-accent-500 flex-shrink-0 text-lg" />
                                            <span>вул. І. Огієнка, 15, Львів</span>
                                        </div>
                                        <a href="tel:+380673851560" className="flex items-center gap-2 hover:text-accent-600 transition-colors">
                                            <FaPhone className="text-accent-500 flex-shrink-0 text-lg" />
                                            <span className="font-semibold">+38 (067) 385 15 60</span>
                                        </a>
                                    </div>
                                    <div className="text-neutral-600 leading-relaxed text-lg space-y-4">
                                        {(category.detailedDescription || category.description).split('\n').map((paragraph: string, index: number) => (
                                            <p key={index}>{paragraph}</p>
                                        ))}
                                    </div>
                                </div>
                            </Reveal>

                            {category.brands && category.brands.length > 0 && (
                                <Reveal delayMs={180}>
                                    <div className="bg-white rounded-2xl p-8 shadow-lg border border-neutral-100">
                                        <h3 className="text-xl font-bold text-neutral-800 mb-6">Працюємо з цими брендами</h3>
                                        <div className="flex flex-wrap gap-6 items-center">
                                            {category.brands.map((brand: any, index: number) => (
                                                <div key={index} className="w-20 h-12 flex items-center justify-center">
                                                    {shouldUseTextLogo(brand.logo) ? (
                                                        <div className="text-center text-sm font-extrabold text-neutral-700 leading-tight">
                                                            {brand.name}
                                                        </div>
                                                    ) : (
                                                        <img
                                                            src={withBasePath(brand.logo)}
                                                            alt={brand.name}
                                                            className="max-w-full max-h-full object-contain"
                                                            loading="lazy"
                                                            width="80"
                                                            height="48"
                                                        />
                                                    )}
                                                </div>
                                            ))}
                                        </div>
                                        <div className="mt-6 flex flex-wrap gap-2">
                                            {category.brands.slice(0, 6).map((brand: any) => (
                                                <Link
                                                    key={brand.name}
                                                    to={`/service/${category.id}/${slugifyBrand(brand.name)}`}
                                                    className="rounded-full bg-primary-50 px-3 py-1 text-sm text-primary-700"
                                                >
                                                    Ремонт {serviceKeyword} {brand.name} у Львові
                                                </Link>
                                            ))}
                                        </div>
                                    </div>
                                </Reveal>
                            )}
                        </div>
                    </div>

                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 mb-16">
                        <Reveal>
                            <div className="bg-white rounded-2xl p-8 shadow-lg border border-neutral-100">
                            <div className="flex items-center mb-6">
                                <Wrench className="w-6 h-6 text-primary-500 mr-3" />
                                <h3 className="text-2xl font-bold text-neutral-800">Ціни на ремонт</h3>
                            </div>
                            <div className="space-y-4">
                                {category.services.map((service: any, index: number) => (
                                    <div key={index} className="flex justify-between items-center py-3 border-b border-neutral-100 last:border-0">
                                        <span className="text-neutral-700 font-medium">{service.name}</span>
                                        <span className="text-primary-600 font-bold whitespace-nowrap ml-4">{service.price}</span>
                                    </div>
                                ))}
                            </div>
                            </div>
                        </Reveal>

                        <Reveal delayMs={120}>
                            <div className="bg-white rounded-2xl p-8 shadow-lg border border-neutral-100">
                            <div className="flex items-center mb-6">
                                <AlertCircle className="w-6 h-6 text-accent-500 mr-3" />
                                <h3 className="text-2xl font-bold text-neutral-800">Типові несправності</h3>
                            </div>
                            <ul className="space-y-4">
                                {category.problems.map((problem: string, index: number) => (
                                    <li key={index} className="flex items-start">
                                        <CheckCircle className="w-5 h-5 text-green-500 mr-3 mt-0.5 flex-shrink-0" />
                                        <span className="text-neutral-600">{problem}</span>
                                    </li>
                                ))}
                            </ul>
                            </div>
                        </Reveal>
                    </div>

                    {faqs.length > 0 && (
                        <Reveal delayMs={160}>
                            <section className="mb-16">
                                <div className="bg-white rounded-2xl p-8 shadow-lg border border-neutral-100">
                                    <h2 className="text-2xl font-bold text-neutral-800 mb-6">Поширені питання</h2>
                                    <div className="space-y-5">
                                        {faqs.map((faq) => (
                                            <div key={faq.question} className="border-b border-neutral-100 pb-5 last:border-0 last:pb-0">
                                                <h3 className="text-lg font-bold text-neutral-800 mb-2">{faq.question}</h3>
                                                <p className="text-neutral-600 leading-relaxed">{faq.answer}</p>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            </section>
                        </Reveal>
                    )}

                    <div className="text-center">
                        <Reveal delayMs={180}>
                            <Link
                                to="/#contact"
                                className="inline-flex items-center justify-center px-8 py-4 text-lg font-bold text-white transition-all duration-300 bg-gradient-to-r from-primary-500 to-primary-600 rounded-xl shadow-lg shadow-primary-500/20 hover:shadow-primary-500/40 hover:scale-[1.02] active:scale-95"
                            >
                                Записатись на ремонт
                            </Link>
                        </Reveal>
                    </div>
                </div>
            </div>
        </>
    );
};

export default ServicePage;
