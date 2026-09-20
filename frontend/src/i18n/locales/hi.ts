import type { Translations } from './en'

export const hi: Translations = {
  app: {
    name: 'किराना स्टोर',
    tagline: 'दुकान का प्रबंधन',
  },
  nav: {
    dashboard: 'डैशबोर्ड',
    products: 'सामान',
    inventory: 'स्टॉक',
    purchases: 'खरीद',
    sales: 'बिक्री',
    customers: 'ग्राहक (खाता)',
    suppliers: 'सप्लायर',
    expenses: 'खर्च',
    reports: 'रिपोर्ट',
  },
  modules: {
    products: {
      description: 'अपने सामान की सूची, दाम, MRP और रीऑर्डर लेवल संभालें।',
    },
    inventory: {
      description: 'मौजूदा स्टॉक, कम स्टॉक की चेतावनी और हर स्टॉक बदलाव का पूरा इतिहास देखें।',
    },
    purchases: {
      description: 'सप्लायर से आया माल दर्ज करें और खरीद वापसी संभालें।',
    },
    sales: {
      description: 'विस्तृत बिल या रोज़ की क्विक बिक्री दर्ज करें, और बिक्री वापसी संभालें।',
    },
    customers: {
      description: 'ग्राहकों और उनके खाते (उधार) का हिसाब रखें।',
    },
    suppliers: {
      description: 'सप्लायर की जानकारी रखें और देखें कि किससे क्या खरीदते हैं।',
    },
    expenses: {
      description: 'किराया, बिजली और वेतन जैसे रोज़ के खर्च दर्ज करें।',
    },
    reports: {
      description: 'बिक्री, खरीद, स्टॉक और मुनाफ़े के अनुमान की रिपोर्ट, CSV/Excel एक्सपोर्ट के साथ।',
    },
  },
  layout: {
    openMenu: 'मेन्यू खोलें',
    closeMenu: 'मेन्यू बंद करें',
    mainNavigation: 'मुख्य मेन्यू',
    language: 'भाषा',
  },
  status: {
    checking: 'सर्वर की जाँच हो रही है…',
    online: 'सर्वर जुड़ा है',
    offline: 'सर्वर से संपर्क नहीं हो पा रहा',
  },
  comingSoon: {
    badge: 'चरण {{phase}} में आएगा',
    message: 'यह मॉड्यूल अभी बना नहीं है। यह रोडमैप के चरण {{phase}} में आएगा।',
  },
  dashboard: {
    title: 'डैशबोर्ड',
    subtitle: 'आपकी दुकान के दिन की झलक।',
    foundationNotice:
      'प्रोजेक्ट की बुनियाद तैयार है। जैसे-जैसे मॉड्यूल बनेंगे, आँकड़े यहाँ दिखेंगे।',
    availableFrom: 'चरण {{phase}} से उपलब्ध',
    kpi: {
      todaysSales: 'आज की बिक्री',
      todaysPurchases: 'आज की खरीद',
      todaysExpenses: 'आज का खर्च',
      estimatedProfit: 'अनुमानित मुनाफ़ा',
      totalProducts: 'कुल सामान',
      stockValue: 'स्टॉक की कीमत',
      lowStock: 'कम स्टॉक वाला सामान',
      outOfStock: 'स्टॉक खत्म वाला सामान',
    },
    panels: {
      salesTrend: 'बिक्री का रुझान',
      lowStockList: 'कम स्टॉक वाला सामान',
    },
  },
  notFound: {
    title: 'पेज नहीं मिला',
    message: 'आप जो पेज ढूँढ रहे हैं वह मौजूद नहीं है।',
    backToDashboard: 'डैशबोर्ड पर जाएँ',
  },
}
