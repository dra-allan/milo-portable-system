const https = require('https');

const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN || '';
const CHAT_ID = process.env.TELEGRAM_CHAT_ID || '8101147332';

if (!BOT_TOKEN) {
  console.error('TELEGRAM_BOT_TOKEN is not set. Set it before running this script.');
  process.exit(1);
}

const videos = [
  { id: 'DJP5hjPPT1E', title: 'How To Produce a Riddim! (Dancehall, Reggae, Reggaeton, Soca, Afro Beat, Zouk) Production Tutorial', channel: 'Paul Hauss', views: '18,998', desc: 'How to build a dancehall riddim from scratch. Contact Paul Hauss for production services.' },
  { id: 'H01eQ_xQ6co', title: 'How to make Modern Zouk Beat | FL Studio tutorial', channel: 'Zouk Craft', views: '4,066', desc: 'FL Studio tutorial for making modern Zouk beats. Sound kits and plugins included.' },
  { id: 'vSG0m6pNIQE', title: 'Omnisphere 3 Is Finally Here - It Sounds So Good', channel: 'SamTheBeardGuy', views: '47,588', desc: 'Omnisphere 3 review. New version of the legendary synth. Sweetwater link.' },
  { id: 'eFMedQGPVNk', title: 'Stop Using Reverb Like This (Do THIS Instead)', channel: 'Streaky', views: '127,387', desc: 'Vocal mixing tips: reverb compression technique, Valhalla Vintage Verb settings.' },
  { id: '1r1lkd6WTQw', title: 'The Secret Mix Buss Technique That Could Change Your Life', channel: 'Help Me Devvon', views: '71,564', desc: 'Mix bus processing technique that works in any DAW (Pro Tools, Logic, FL, Ableton, Cubase).' },
  { id: 'Kjsku8HR73s', title: '9 Logic Pro Tips That Feel Like Cheating', channel: 'Nathan James Larsen', views: '32,892', desc: 'Logic Pro tips. Larsen Lab community for feedback sessions and monthly trainings.' },
  { id: 'E8LITZg-TXo', title: '24 Small Ableton Live 12 changes that make a BIG Difference!', channel: 'Brian Funk', views: '98,718', desc: 'Small features in Ableton Live 12 that make a big difference in workflow.' },
  { id: 'TpEkCPyi69c', title: 'An Ableton Productivity Hack You Might Not Know About (ALC Files)', channel: 'Seed To Stage', views: '23,033', desc: 'ALC files in Ableton - a productivity hack for faster music production.' },
  { id: '7rWYmBw06H0', title: 'Buli producer wetaaga plugins zino (every producer needs these plugins)', channel: 'studio expert Kampala ug', views: '126', desc: 'Essential plugins for music and video producers - Kampala based.' }
];

const money = [
  { id: 'id1rzzJsQ98', title: 'How Much I Make Owning a 5th Division Kenyan Club - Road to the KPL S2 Ep 2', channel: 'JerseyBird', views: '832,227', desc: 'Behind the scenes finances of Kahawa Pride FC, a 5th division Kenyan football club.' },
  { id: 'ngjYe5KTVqM', title: 'HOW WE GOT Monetized in 48 HOURS | COPY & PASTE METHOD', channel: 'Cash-Coach', views: '564,430', desc: 'Faceless YouTube channel automation. Skool training program for beginners.' },
  { id: 'MSS41hSN7ZM', title: 'I Made $296,792 with 1 Faceless YouTube Channel...', channel: 'Romayroh', views: '39,848', desc: 'Faceless YouTube channel case study. Skool community "Views for Income".' }
];

const tech = [
  { id: 'oWRI6xKEZMk', title: 'How Hackers Hack Websites', channel: 'Neurix', views: '1,336,500', desc: 'Website hacking techniques: penetration testing, Kali Linux, web enumeration. No zero-days needed.' },
  { id: 'iQ7r8d3k_lY', title: 'The Dark Truth About Lucky Patcher', channel: 'Percival', views: '598,707', desc: 'How Lucky Patcher became the most controversial Android app with its illegal functions.' },
  { id: 'Y9JF_yYTNlw', title: 'The BEST Smartphones of 2025!', channel: 'Mrwhosetheboss', views: '3,551,897', desc: 'Best smartphone awards for 2025. Torras Ostand case recommended.' }
];

const other = [
  { id: '7W89eklcj3c', title: 'How to Make a Cheap Camera Cinematic', channel: 'Zachary Silva', views: '1,605,186', desc: 'Make old/cheap cameras look cinematic. Coaching calls available. Audiio music.' },
  { id: 'CDeB98AjJY0', title: 'What Really Happens After 30 Days of No Nut November', channel: 'Rena Malik, M.D.', views: '3,161,842', desc: 'Medical perspective on No Nut November and semen retention. Urologist explains.' },
  { id: 'kZNnz8ifd7c', title: 'UPDF etimpudde Al Shabab', channel: 'Bukedde TV', views: '627,546', desc: 'Uganda coverage - UPDF operations against Al Shabab in Somalia.' },
  { id: '1EYUhpimyxc', title: 'Why can parrots talk? - Grace Smith-Vidaurre and Tim Wright', channel: 'TED-Ed', views: '1,777,202', desc: 'The specialized anatomy that allows parrots to talk, scream, and mimic human speech.' },
  { id: 'RTrMYZzD_CM', title: '9 Failed Superhero Franchises & What Happened To Them', channel: 'The Gold Man', views: '342,245', desc: 'Deep dive into superhero franchises that failed despite Hollywood dominance.' }
];

function sendTelegram(text) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify({ chat_id: CHAT_ID, text: text, parse_mode: 'HTML' });
    const req = https.request({
      hostname: 'api.telegram.org',
      path: '/bot' + BOT_TOKEN + '/sendMessage',
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) }
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve(JSON.parse(d)));
    });
    req.on('error', reject);
    req.write(payload);
    req.end();
  });
}

function formatGroup(list, header) {
  let msg = '<b>' + header + '</b>\n\n';
  list.forEach((v, i) => {
    msg += (i + 1) + '. <b>' + v.title.substring(0, 60).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</b>\n';
    msg += '   Channel: ' + v.channel + ' · Views: ' + v.views + '\n';
    msg += '   ' + v.desc.substring(0, 120) + '\n';
    msg += '   https://youtu.be/' + v.id + '\n\n';
  });
  return msg;
}

(async () => {
  const groups = [
    formatGroup(videos, '\uD83C\uDFB6 Music Production (9 videos)'),
    formatGroup(money, '\uD83D\uDCB0 YouTube / Money (3 videos)'),
    formatGroup(tech, '\uD83D\uDDA5\uFE0F Tech / Security (3 videos)'),
    formatGroup(other, '\uD83D\uDCF1 Other (5 videos)')
  ];

  for (const msg of groups) {
    if (msg.length > 4000) {
      // Split into chunks
      const lines = msg.split('\n');
      let chunk = '';
      for (const line of lines) {
        if (chunk.length + line.length > 3900) {
          const r = await sendTelegram(chunk);
          console.log('Sent chunk: ' + (r.ok ? 'OK' : 'FAIL'));
          chunk = line + '\n';
        } else {
          chunk += line + '\n';
        }
      }
      if (chunk) {
        const r = await sendTelegram(chunk);
        console.log('Sent final chunk: ' + (r.ok ? 'OK' : 'FAIL'));
      }
    } else {
      const r = await sendTelegram(msg);
      console.log('Sent ' + headerFromMsg(msg) + ': ' + (r.ok ? 'OK' : 'FAIL'));
    }
    await new Promise(r => setTimeout(r, 1000)); // Rate limit
  }
  console.log('\nAll summaries sent to Telegram!');
})();

function headerFromMsg(msg) {
  const m = msg.match(/<b>([^<]+)<\/b>/);
  return m ? m[1] : 'message';
}
