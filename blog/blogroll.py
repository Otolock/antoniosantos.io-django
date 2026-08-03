LINK_GROUPS = [
    {
        "eyebrow": "People to follow",
        "title": "Blogs I enjoy",
        "description": "Personal sites, thoughtful writing, and good corners of the web.",
        "links": [
            {
                "title": "Rhys Wynne",
                "url": "http://www.rhyswynne.co.uk/",
                "domain": "rhyswynne.co.uk",
                "initial": "R",
            },
            {
                "title": "James’ Coffee Blog",
                "url": "https://jamesg.blog/",
                "domain": "jamesg.blog",
                "initial": "J",
            },
            {
                "title": "Michael Harley",
                "url": "https://michaelharley.net/",
                "domain": "michaelharley.net",
                "initial": "M",
            },
            {
                "title": "Sals.place",
                "url": "https://sals.place/",
                "domain": "sals.place",
                "initial": "S",
            },
            {
                "title": "Jshmnrd",
                "url": "https://jshmnrd.ca/",
                "domain": "jshmnrd.ca",
                "initial": "J",
            },
            {
                "title": "Michael Reflects",
                "url": "https://michaelreflects.com/",
                "domain": "michaelreflects.com",
                "initial": "M",
            },
        ],
    },
    {
        "eyebrow": "Worth your attention",
        "title": "Articles I’ve read recently",
        "description": "A few links that stayed with me after I closed the tab.",
        "links": [
            {
                "title": "Re: Bubbles, interlinking, etc.",
                "description": "On making the web feel connected again.",
                "url": "https://sals.place/blog/re-bubbles-interlinking-etc/",
                "domain": "sals.place/blog",
                "initial": "↗",
            },
            {
                "title": "So I’m a gangster now?",
                "description": "A story with a title that makes you want to know more.",
                "url": "https://www.taiwanquest.com/so-im-a-gangster-now/",
                "domain": "taiwanquest.com",
                "initial": "↗",
            },
            {
                "title": "Where to find the colors your screen can’t show you",
                "description": "A beautiful rabbit hole into color and perception.",
                "url": "https://moultano.wordpress.com/2026/06/19/where-to-find-the-colors-your-screen-cant-show-you/",
                "domain": "moultano.wordpress.com",
                "initial": "↗",
            },
            {
                "title": "If You are Asking for Human Attention, Demonstrate Human Effort",
                "description": "When is it OK to forward the output of an AI to another human to read?",
                "url": "https://tombedor.dev/human-attention-and-human-effort/",
                "domain": "tombedor.dev",
                "initial": "↗",
            },
            {
                "title": "Treat your to-read pile like a river, not a bucket",
                "description": "This post changed how I manage my to-read list.",
                "url": "https://www.oliverburkeman.com/river",
                "domain": "oliverburkeman.com",
                "initial": "↗",
            },
        ],
    },
]

LINK_COUNT = sum(len(group["links"]) for group in LINK_GROUPS)
