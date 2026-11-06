"""Lecture and note languages, and the few fixed sentences the app itself writes into a note.

Codes are ISO 639-1, which is also what Whisper-style transcription APIs accept as a hint.
"""

# code: (English name used in prompts, name shown to people)
LANGUAGES = {
    'en': ('English', 'English'),
    'zh': ('Simplified Chinese', '简体中文'),
    'es': ('Spanish', 'Español'),
    'fr': ('French', 'Français'),
    'de': ('German', 'Deutsch'),
    'ja': ('Japanese', '日本語'),
    'ko': ('Korean', '한국어'),
    'pt': ('Portuguese', 'Português'),
    'ru': ('Russian', 'Русский'),
    'ar': ('Arabic', 'العربية'),
}
AUTO = 'auto'
DEFAULT_SOURCE = 'en'
DEFAULT_TARGET = 'zh'


def valid_source(code):
    return code == AUTO or code in LANGUAGES


def valid_target(code):
    return code in LANGUAGES


def name(code):
    return LANGUAGES[code][0] if code in LANGUAGES else "the lecture's language"


def needs_translation(source, target):
    """An auto-detected lecture may already be in the note language; the prompt copies those segments as-is."""
    return source != target


# Fixed notices in the note's own language. {0}, {1}, {2} are filled by processing.compose_note.
NOTICES = {
    'zh': {
        'heading_pair': '## Transcript 原始转写（中英对照）',
        'heading': '## Transcript 原始转写',
        'missing': '> ⚠ 原始转写有 {0} / {1} 段没有生成中文对照{2}；原始转写已保留，可点“重新生成笔记”补全。\n\n',
        'quality': '> ⚠ 录音质量可能有问题：{0}% 的分段未识别到语音，最长连续 {1} 段；分段长度不同，此比例不是静音时长比例。'
                   '笔记只基于识别到的部分，请对照原音频。\n\n',
    },
    'en': {
        'heading_pair': '## Transcript (with translation)',
        'heading': '## Transcript',
        'missing': '> ⚠ {0} of {1} transcript segments have no translation{2}. The original transcript is kept; use "Regenerate note" to fill them in.\n\n',
        'quality': '> ⚠ Possible recording problem: no speech was recognised in {0}% of segments, up to {1} in a row. '
                   'Segments vary in length, so this is not the share of silent time. The note only covers what was recognised; check the original audio.\n\n',
    },
    'es': {
        'heading_pair': '## Transcripción (con traducción)',
        'heading': '## Transcripción',
        'missing': '> ⚠ {0} de {1} segmentos de la transcripción no tienen traducción{2}. Se conserva la transcripción original; usa «Regenerar nota» para completarlos.\n\n',
        'quality': '> ⚠ Posible problema de grabación: no se reconoció voz en el {0}% de los segmentos, hasta {1} seguidos. '
                   'La nota solo cubre lo reconocido; revisa el audio original.\n\n',
    },
    'fr': {
        'heading_pair': '## Transcription (avec traduction)',
        'heading': '## Transcription',
        'missing': '> ⚠ {0} segments sur {1} n’ont pas de traduction{2}. La transcription originale est conservée ; utilisez « Régénérer la note » pour les compléter.\n\n',
        'quality': '> ⚠ Problème d’enregistrement possible : aucune parole reconnue dans {0} % des segments, jusqu’à {1} d’affilée. '
                   'La note ne couvre que ce qui a été reconnu ; vérifiez l’audio original.\n\n',
    },
    'de': {
        'heading_pair': '## Transkript (mit Übersetzung)',
        'heading': '## Transkript',
        'missing': '> ⚠ {0} von {1} Transkript-Abschnitten haben keine Übersetzung{2}. Das Originaltranskript bleibt erhalten; mit „Notiz neu erstellen“ lassen sie sich ergänzen.\n\n',
        'quality': '> ⚠ Mögliches Aufnahmeproblem: In {0} % der Abschnitte wurde keine Sprache erkannt, bis zu {1} in Folge. '
                   'Die Notiz deckt nur Erkanntes ab; bitte das Originalaudio prüfen.\n\n',
    },
    'ja': {
        'heading_pair': '## 文字起こし（翻訳付き）',
        'heading': '## 文字起こし',
        'missing': '> ⚠ 文字起こし {1} 区間のうち {0} 区間に翻訳がありません{2}。元の文字起こしは保存されています。「ノートを再生成」で補えます。\n\n',
        'quality': '> ⚠ 録音に問題がある可能性：{0}% の区間で音声を認識できず、最大 {1} 区間連続しました。'
                   'ノートは認識できた部分のみに基づいています。元の音声を確認してください。\n\n',
    },
    'ko': {
        'heading_pair': '## 전사본 (번역 포함)',
        'heading': '## 전사본',
        'missing': '> ⚠ 전사 구간 {1}개 중 {0}개에 번역이 없습니다{2}. 원본 전사는 보존되어 있으며 “노트 다시 생성”으로 채울 수 있습니다.\n\n',
        'quality': '> ⚠ 녹음 문제 가능성: 구간의 {0}%에서 음성이 인식되지 않았고, 최대 {1}개 구간이 연속되었습니다. '
                   '노트는 인식된 부분만 바탕으로 합니다. 원본 오디오를 확인하세요.\n\n',
    },
    'pt': {
        'heading_pair': '## Transcrição (com tradução)',
        'heading': '## Transcrição',
        'missing': '> ⚠ {0} de {1} segmentos da transcrição estão sem tradução{2}. A transcrição original foi mantida; use “Regenerar nota” para completá-los.\n\n',
        'quality': '> ⚠ Possível problema na gravação: nenhuma fala reconhecida em {0}% dos segmentos, até {1} seguidos. '
                   'A nota cobre apenas o que foi reconhecido; confira o áudio original.\n\n',
    },
    'ru': {
        'heading_pair': '## Расшифровка (с переводом)',
        'heading': '## Расшифровка',
        'missing': '> ⚠ {0} из {1} фрагментов расшифровки без перевода{2}. Исходная расшифровка сохранена; нажмите «Пересоздать заметку», чтобы дополнить.\n\n',
        'quality': '> ⚠ Возможна проблема с записью: речь не распознана в {0}% фрагментов, до {1} подряд. '
                   'Заметка основана только на распознанном; проверьте исходное аудио.\n\n',
    },
    'ar': {
        'heading_pair': '## النص المفرّغ (مع الترجمة)',
        'heading': '## النص المفرّغ',
        'missing': '> ⚠ {0} من أصل {1} مقطعًا بلا ترجمة{2}. النص الأصلي محفوظ؛ استخدم «إعادة إنشاء الملاحظة» لإكمالها.\n\n',
        'quality': '> ⚠ قد تكون هناك مشكلة في التسجيل: لم يُتعرَّف على كلام في {0}% من المقاطع، حتى {1} مقاطع متتالية. '
                   'الملاحظة مبنية على ما تم التعرف عليه فقط؛ راجع التسجيل الأصلي.\n\n',
    },
}


def notice(target, key):
    return NOTICES.get(target, NOTICES['en'])[key]
