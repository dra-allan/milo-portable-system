# PLAN: Ranking General Commentary Niche for YouTube Shorts Pipeline

## Overview
This plan outlines the configuration and strategy for adding a "ranking_general_commentary" niche to the YouTube Shorts pipeline. This niche will focus on top-10 lists, countdown videos, rankings, and comparative commentary content that performs well in short-form format.

## Niche Configuration Template
Based on analysis of existing niches (flick_shorts, capital_mindset), here's the proposed configuration:

```yaml
ranking_general_commentary:
  upload_channels:
    - ranking_general_commentary  # Must match youtube_token_ranking_general_commentary.json
  channel: ranking_general_commentary  # Legacy binding
  max_videos: 3  # Per-run video cap
  channels:
    - "@WatchMojo"
    - "@TheRichest"
    - "@TopTrending"
    - "@ScreenRant"
    - "@Looper"
    - "@TheTalko"
    - "@GradeAUnderA"
    - "@Jacksfilms"
    - "@CommentsOverload"
    - "@MostAmazingTop10"
    - "@Top5Best"
    - "@AmazingTop10"
    - "@FactFile"
    - "@Alltime10s"
    - "@TopTenz"
  keywords:
    - "top 10"
    - "top 5"
    - "top 3"
    - "countdown"
    - "listicle"
    - "ranking"
    - "most expensive"
    - "largest"
    - "smallest"
    - "fastest"
    - "slowest"
    - "deadliest"
    - "dangerous"
    - "richest"
    - "poorest"
    - "smartest"
    - "dumbest"
    - "strangest"
    - "weirdest"
    - "craziest"
    - "unbelievable"
    - "incredible"
    - "amazing"
    - "shocking"
    - "surprising"
    - "you won't believe"
    - "never knew"
    - "hidden facts"
    - "little known"
    - "rarely seen"
    - "best of"
    - "worst of"
    - "biggest"
    - "highest"
    - "lowest"
    - "tallest"
    - "shortest"
    - "oldest"
    - "newest"
    - "first ever"
    - "last"
    - "final"
    - "original"
    - "copycat"
    - "knockoff"
    - "rip off"
    - "inspired by"
    - "similar to"
    - "compared to"
    - "versus"
    - "vs"
    - "better than"
    - "worse than"
    - "cheaper than"
    - "more expensive than"
    - "bigger than"
    - "smaller than"
    - "longer than"
    - "shorter than"
    - "faster than"
    - "slower than"
  negative_keywords: ["#shorts", "shorts", "clip", "clips", "podcast clips", "compilation", "reaction", "trailer", "teaser", "livestream", "live stream", "music video", "official audio", "lyrics", "fan edit", "meme", "music", "dance", "concert", "church service", "preaching", "choir", "audiobook", "lecture", "tutorial", "how to", "guide", "walkthrough", "gameplay", "live", "breaking news"]
  language: en
  min_duration: 300  # 5 minutes minimum - ranking videos need substance
  max_duration: 10800  # 3 hours maximum
  min_score: 0.48  # Slightly lower threshold as ranking content can be dense but scannable
  preferred_upload_days: 365  # Evergreen content - check sources yearly
  min_views: 50000  # Lower threshold as ranking channels often get views distributed across many videos
```

## Content Strategy Insights

### What Makes Ranking Content Work for Shorts:
1. **Clear Structure**: Top 10 format provides natural segmentation for short clips
2. **Curiosity Gap**: "Number 1 will shock you" creates anticipation
3. **Scannability**: Viewers can grasp value quickly even in 15-60 second clips
4. **Evergreen Potential**: Many ranking topics remain relevant for years
5. **Algorithmic Friendly**: High retention when viewers wait for the #1 spot
6. **Shareability**: Easy to discuss "Did you see #3 on that list?"

### Successful Patterns from Research:
1. **Hook-First Approach**: Start with the most surprising/shocking item
2. **Progressive Reveal**: Build anticipation from #10 to #1
3. **Visual Variety**: Each item needs distinct visuals or clips
4. **Concise Explanation**: 1-2 sentences max per item in short format
5. **Strong #1 Position**: Save most impressive/compelling for number 1
6. **Controversy Element**: Include debatable rankings to drive engagement
7. **Niche Specificity**: "Top 10 Most Expensive Errors in History" vs generic "Top 10 Mistakes"

### Source Channel Analysis:
- **WatchMojo**: Pioneer in top 10 format, massive library
- **TheRichest**: Focus on wealth, luxury, celebrity rankings
- **TopTrending/ScreenRant**: Entertainment, movies, TV rankings
- **Looper**: Movie-focused rankings and trivia
- **GradeAUnderA/Jacksfilms**: Commentary-style rankings with humor
- **CommentsOverload**: Reddit-story based rankings
- **MostAmazingTop10/FactFile**: Educational/scientific rankings
- **Alltime10s/TopTenz**: Variety of topics with consistent format

### Title Optimization Strategy:
With TITLE_OPTIMIZER=true enabled, raw hooks should be:
- "You Won't Believe #1"
- "Number 1 Will Shock You"
- "I Ranked Them From Worst to Best"
- "The Truth About #1"
- "What Nobody Tells You About #1"
- "Most People Get This Wrong"
- "This Changes Everything About #1"
- "The Dark Truth About #1"

### Scoring Algorithm Considerations:
The processor.py scoring system should favor:
- Speech density (clear enumeration: "Number 10:", "Coming in at #9:")
- Ranking keywords ("top", "best", "worst", "most", "least")
- Numerical presence (digits 1-10 written or spoken)
- Comparative language ("better than", "worse than", "compared to")
- Superlatives ("biggest", "smallest", "fastest", "slowest")
- Hook phrases from existing flick_shorts keywords (many overlap well)

## Implementation Steps:

1. **Authentication**: User must run `python -m src.uploader auth --channel ranking_general_commentary`
2. **Configuration**: Add the above block to niches.yaml
3. **Testing**: 
   - Run discovery test: `python -m src.main --mode discover --niche ranking_general_commentary`
   - Process one video: `python -m src.main --mode once --niche ranking_general_commentary --target <test_URL>`
   - Verify output quality in data/shorts/ranking_general_commentary/
4. **Monitoring**: Check statistics after first few runs to adjust min_score/keywords if needed
5. **Integration**: Will automatically participate in scheduled sweeps with existing niches

## Expected Performance:
- **View Potential**: Ranking content often performs well due to binge-watchability
- **Evergreen Value**: Many topics remain searchable for years
- **Cross-appeal**: Works across demographics when topics are universally interesting
- **Algorithm Friendly**: High completion rates when viewers wait for #1

## Risk Mitigation:
- **Copyright**: Ensure transformation is sufficient (commentary, criticism, education)
- **Quality Control**: Negative keywords help filter low-effort source videos
- **Saturation**: Mix evergreen and timely topics to avoid repetition
- **Quality Variance**: Some ranking channels have inconsistent quality - negative keywords help filter

## Relationship to Existing Niches:
- Complements flick_shorts (both cover commentary/analysis content)
- Differentiates by focusing on list/ranking format vs deep-dives
- Can share some source channels but with different keyword targeting
- May appeal to similar audience as capital_mindset but with different delivery format

## Vault Storage Instruction:
As requested, the plans for Roblox, copcam commentary, and Minecraft niches will be saved to the vault for later implementation.