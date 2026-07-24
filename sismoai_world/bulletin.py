from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LANGUAGES: dict[str, dict[str, Any]] = {
    "es": {
        "name": "Español",
        "voice": "es",
        "states": {
            "NORMAL": "normal",
            "WATCH": "en observación",
            "ELEVATED": "elevado",
            "HIGHLY_ATYPICAL": "altamente atípico",
            "NO_DATA": "sin datos",
        },
        "classes": {
            "OBSERVED_ACTIVITY": "Actividad observada",
            "STATISTICAL_ANOMALY": "Anomalía estadística",
            "CANDIDATE_PATTERN": "Patrón candidato",
            "EXPERIMENTAL_SIGNAL": "Señal experimental prioritaria",
        },
        "headline": "Boletín SismoAI",
        "summary": (
            "Resumen de estados: normal {normal}; observación {watch}; elevado {elevated}; "
            "altamente atípico {atypical}. Regiones operativas: {total}."
        ),
        "no_previous": "Esta es la primera actualización disponible para la comparación automática.",
        "no_state_changes": "No hubo cambios de nivel desde la actualización anterior.",
        "state_changes": "Cambios de nivel desde la actualización anterior: {items}.",
        "change_item": "{region} pasó de {before} a {after}",
        "region": "{region} está {state}, con IEDC provisional {iedc} y confianza {confidence}. {reason} {event}",
        "reason_count": "La tasa sísmica reciente se apartó de su referencia regional.",
        "reason_magnitude": "La magnitud máxima reciente se apartó de su referencia regional.",
        "reason_generic": "El cálculo detectó una desviación estadística en la ventana reciente.",
        "event": "El evento más reciente listado es de magnitud {magnitude}, el {date}, en {place}.",
        "no_notable": "Ninguna región supera actualmente el nivel normal.",
        "history": (
            "El laboratorio histórico ha procesado {progress} desde 1973: {events} eventos USGS "
            "de magnitud 4.5 o superior y {patterns} patrones candidatos. Esos patrones son solo investigación."
        ),
        "gate_closed": (
            "No existe una señal experimental aprobada. El gate público está cerrado y esto no es "
            "una predicción, una alerta oficial ni una orden de evacuación."
        ),
        "signal": (
            "SEÑAL EXPERIMENTAL PRIORITARIA — NO ES ALERTA OFICIAL. {region}, ventana {window}, "
            "objetivo {target}, probabilidad experimental {probability} frente a una referencia de {baseline}. "
            "Consulte siempre a las autoridades oficiales."
        ),
        "candidate": "El laboratorio mantiene {patterns} patrones candidatos separados del sistema operativo.",
        "latest": "Última actualización",
        "changes": "Qué cambió",
        "situation": "Situación actual",
        "limitations": "Alcance y limitaciones",
        "history_title": "Memoria y patrones",
        "listen": "Escuchar boletín",
        "pause": "Pausar",
        "resume": "Continuar",
        "stop": "Detener",
        "no_voice": "Este dispositivo no tiene una voz disponible para el idioma seleccionado.",
        "auto": "Automático",
        "archive": "Archivo de boletines",
        "official": "No es una alerta oficial",
    },
    "en": {
        "name": "English",
        "voice": "en",
        "states": {
            "NORMAL": "normal",
            "WATCH": "under watch",
            "ELEVATED": "elevated",
            "HIGHLY_ATYPICAL": "highly atypical",
            "NO_DATA": "without data",
        },
        "classes": {
            "OBSERVED_ACTIVITY": "Observed activity",
            "STATISTICAL_ANOMALY": "Statistical anomaly",
            "CANDIDATE_PATTERN": "Candidate pattern",
            "EXPERIMENTAL_SIGNAL": "Priority experimental signal",
        },
        "headline": "SismoAI Bulletin",
        "summary": (
            "State totals: normal {normal}; watch {watch}; elevated {elevated}; highly atypical {atypical}. "
            "Operational regions: {total}."
        ),
        "no_previous": "This is the first update available for automatic comparison.",
        "no_state_changes": "There were no level changes since the previous update.",
        "state_changes": "Level changes since the previous update: {items}.",
        "change_item": "{region} moved from {before} to {after}",
        "region": "{region} is {state}, with provisional IEDC {iedc} and {confidence} confidence. {reason} {event}",
        "reason_count": "The recent seismic rate moved away from its regional baseline.",
        "reason_magnitude": "The recent maximum magnitude moved away from its regional baseline.",
        "reason_generic": "The calculation detected a statistical deviation in the recent window.",
        "event": "The latest listed event is magnitude {magnitude}, on {date}, at {place}.",
        "no_notable": "No region is currently above the normal level.",
        "history": (
            "The historical laboratory has processed {progress} since 1973: {events} USGS events "
            "of magnitude 4.5 or higher and {patterns} candidate patterns. Those patterns are research only."
        ),
        "gate_closed": (
            "There is no approved experimental signal. The public gate is closed, and this is not "
            "a prediction, an official alert, or an evacuation order."
        ),
        "signal": (
            "PRIORITY EXPERIMENTAL SIGNAL — NOT AN OFFICIAL ALERT. {region}, window {window}, "
            "target {target}, experimental probability {probability} versus a {baseline} baseline. "
            "Always consult official authorities."
        ),
        "candidate": "The laboratory maintains {patterns} candidate patterns separate from the operational system.",
        "latest": "Latest update",
        "changes": "What changed",
        "situation": "Current situation",
        "limitations": "Scope and limitations",
        "history_title": "Memory and patterns",
        "listen": "Listen to bulletin",
        "pause": "Pause",
        "resume": "Resume",
        "stop": "Stop",
        "no_voice": "This device has no available voice for the selected language.",
        "auto": "Automatic",
        "archive": "Bulletin archive",
        "official": "Not an official alert",
    },
    "pt": {
        "name": "Português",
        "voice": "pt",
        "states": {
            "NORMAL": "normal", "WATCH": "em observação", "ELEVATED": "elevado",
            "HIGHLY_ATYPICAL": "altamente atípico", "NO_DATA": "sem dados",
        },
        "classes": {
            "OBSERVED_ACTIVITY": "Atividade observada", "STATISTICAL_ANOMALY": "Anomalia estatística",
            "CANDIDATE_PATTERN": "Padrão candidato", "EXPERIMENTAL_SIGNAL": "Sinal experimental prioritário",
        },
        "headline": "Boletim SismoAI",
        "summary": (
            "Resumo dos estados: normal {normal}; observação {watch}; elevado {elevated}; "
            "altamente atípico {atypical}. Regiões operacionais: {total}."
        ),
        "no_previous": "Esta é a primeira atualização disponível para comparação automática.",
        "no_state_changes": "Não houve mudanças de nível desde a atualização anterior.",
        "state_changes": "Mudanças de nível desde a atualização anterior: {items}.",
        "change_item": "{region} passou de {before} para {after}",
        "region": "{region} está {state}, com IEDC provisório {iedc} e confiança {confidence}. {reason} {event}",
        "reason_count": "A taxa sísmica recente se afastou da referência regional.",
        "reason_magnitude": "A magnitude máxima recente se afastou da referência regional.",
        "reason_generic": "O cálculo detectou um desvio estatístico na janela recente.",
        "event": "O evento mais recente listado é de magnitude {magnitude}, em {date}, em {place}.",
        "no_notable": "Nenhuma região está atualmente acima do nível normal.",
        "history": (
            "O laboratório histórico processou {progress} desde 1973: {events} eventos USGS de magnitude "
            "4.5 ou superior e {patterns} padrões candidatos. Esses padrões são apenas pesquisa."
        ),
        "gate_closed": (
            "Não existe sinal experimental aprovado. O gate público está fechado e isto não é uma "
            "previsão, alerta oficial ou ordem de evacuação."
        ),
        "signal": (
            "SINAL EXPERIMENTAL PRIORITÁRIO — NÃO É ALERTA OFICIAL. {region}, janela {window}, "
            "alvo {target}, probabilidade experimental {probability} ante referência de {baseline}. "
            "Consulte sempre as autoridades oficiais."
        ),
        "candidate": "O laboratório mantém {patterns} padrões candidatos separados do sistema operacional.",
        "latest": "Última atualização", "changes": "O que mudou", "situation": "Situação atual",
        "limitations": "Escopo e limitações", "history_title": "Memória e padrões",
        "listen": "Ouvir boletim", "pause": "Pausar", "resume": "Continuar", "stop": "Parar",
        "no_voice": "Este dispositivo não possui voz para o idioma selecionado.",
        "auto": "Automático", "archive": "Arquivo de boletins", "official": "Não é um alerta oficial",
    },
    "fr": {
        "name": "Français",
        "voice": "fr",
        "states": {
            "NORMAL": "normal", "WATCH": "sous surveillance", "ELEVATED": "élevé",
            "HIGHLY_ATYPICAL": "très atypique", "NO_DATA": "sans données",
        },
        "classes": {
            "OBSERVED_ACTIVITY": "Activité observée", "STATISTICAL_ANOMALY": "Anomalie statistique",
            "CANDIDATE_PATTERN": "Motif candidat", "EXPERIMENTAL_SIGNAL": "Signal expérimental prioritaire",
        },
        "headline": "Bulletin SismoAI",
        "summary": (
            "Totaux par état : normal {normal} ; surveillance {watch} ; élevé {elevated} ; "
            "très atypique {atypical}. Régions opérationnelles : {total}."
        ),
        "no_previous": "Il s’agit de la première mise à jour disponible pour la comparaison automatique.",
        "no_state_changes": "Aucun changement de niveau depuis la mise à jour précédente.",
        "state_changes": "Changements de niveau depuis la mise à jour précédente : {items}.",
        "change_item": "{region} est passée de {before} à {after}",
        "region": "{region} est {state}, avec un IEDC provisoire de {iedc} et une confiance de {confidence}. {reason} {event}",
        "reason_count": "Le taux sismique récent s’est écarté de sa référence régionale.",
        "reason_magnitude": "La magnitude maximale récente s’est écartée de sa référence régionale.",
        "reason_generic": "Le calcul a détecté un écart statistique dans la fenêtre récente.",
        "event": "Le dernier événement répertorié est de magnitude {magnitude}, le {date}, à {place}.",
        "no_notable": "Aucune région ne dépasse actuellement le niveau normal.",
        "history": (
            "Le laboratoire historique a traité {progress} depuis 1973 : {events} événements USGS de magnitude "
            "4,5 ou plus et {patterns} motifs candidats. Ces motifs sont réservés à la recherche."
        ),
        "gate_closed": (
            "Il n’existe aucun signal expérimental approuvé. Le gate public est fermé et ceci n’est ni "
            "une prédiction, ni une alerte officielle, ni un ordre d’évacuation."
        ),
        "signal": (
            "SIGNAL EXPÉRIMENTAL PRIORITAIRE — PAS UNE ALERTE OFFICIELLE. {region}, fenêtre {window}, "
            "cible {target}, probabilité expérimentale {probability} contre une référence de {baseline}. "
            "Consultez toujours les autorités officielles."
        ),
        "candidate": "Le laboratoire conserve {patterns} motifs candidats séparés du système opérationnel.",
        "latest": "Dernière mise à jour", "changes": "Ce qui a changé", "situation": "Situation actuelle",
        "limitations": "Portée et limites", "history_title": "Mémoire et motifs",
        "listen": "Écouter le bulletin", "pause": "Pause", "resume": "Reprendre", "stop": "Arrêter",
        "no_voice": "Cet appareil ne dispose pas de voix pour la langue sélectionnée.",
        "auto": "Automatique", "archive": "Archives des bulletins", "official": "Pas une alerte officielle",
    },
    "it": {
        "name": "Italiano",
        "voice": "it",
        "states": {
            "NORMAL": "normale", "WATCH": "sotto osservazione", "ELEVATED": "elevato",
            "HIGHLY_ATYPICAL": "altamente atipico", "NO_DATA": "senza dati",
        },
        "classes": {
            "OBSERVED_ACTIVITY": "Attività osservata", "STATISTICAL_ANOMALY": "Anomalia statistica",
            "CANDIDATE_PATTERN": "Schema candidato", "EXPERIMENTAL_SIGNAL": "Segnale sperimentale prioritario",
        },
        "headline": "Bollettino SismoAI",
        "summary": (
            "Totali per stato: normale {normal}; osservazione {watch}; elevato {elevated}; "
            "altamente atipico {atypical}. Regioni operative: {total}."
        ),
        "no_previous": "Questo è il primo aggiornamento disponibile per il confronto automatico.",
        "no_state_changes": "Nessun cambio di livello rispetto all’aggiornamento precedente.",
        "state_changes": "Cambi di livello rispetto all’aggiornamento precedente: {items}.",
        "change_item": "{region} è passata da {before} a {after}",
        "region": "{region} è {state}, con IEDC provvisorio {iedc} e confidenza {confidence}. {reason} {event}",
        "reason_count": "Il tasso sismico recente si è discostato dal riferimento regionale.",
        "reason_magnitude": "La magnitudo massima recente si è discostata dal riferimento regionale.",
        "reason_generic": "Il calcolo ha rilevato una deviazione statistica nella finestra recente.",
        "event": "L’ultimo evento elencato è di magnitudo {magnitude}, il {date}, a {place}.",
        "no_notable": "Nessuna regione supera attualmente il livello normale.",
        "history": (
            "Il laboratorio storico ha elaborato il {progress} dal 1973: {events} eventi USGS di magnitudo "
            "4,5 o superiore e {patterns} schemi candidati. Questi schemi sono solo ricerca."
        ),
        "gate_closed": (
            "Non esiste alcun segnale sperimentale approvato. Il gate pubblico è chiuso e questa non è "
            "una previsione, un’allerta ufficiale o un ordine di evacuazione."
        ),
        "signal": (
            "SEGNALE SPERIMENTALE PRIORITARIO — NON È UN’ALLERTA UFFICIALE. {region}, finestra {window}, "
            "obiettivo {target}, probabilità sperimentale {probability} rispetto a {baseline}. "
            "Consultare sempre le autorità ufficiali."
        ),
        "candidate": "Il laboratorio mantiene {patterns} schemi candidati separati dal sistema operativo.",
        "latest": "Ultimo aggiornamento", "changes": "Cosa è cambiato", "situation": "Situazione attuale",
        "limitations": "Ambito e limiti", "history_title": "Memoria e schemi",
        "listen": "Ascolta il bollettino", "pause": "Pausa", "resume": "Riprendi", "stop": "Ferma",
        "no_voice": "Questo dispositivo non dispone di una voce per la lingua selezionata.",
        "auto": "Automatico", "archive": "Archivio bollettini", "official": "Non è un’allerta ufficiale",
    },
    "de": {
        "name": "Deutsch",
        "voice": "de",
        "states": {
            "NORMAL": "normal", "WATCH": "unter Beobachtung", "ELEVATED": "erhöht",
            "HIGHLY_ATYPICAL": "stark atypisch", "NO_DATA": "ohne Daten",
        },
        "classes": {
            "OBSERVED_ACTIVITY": "Beobachtete Aktivität", "STATISTICAL_ANOMALY": "Statistische Anomalie",
            "CANDIDATE_PATTERN": "Kandidatenmuster", "EXPERIMENTAL_SIGNAL": "Prioritäres experimentelles Signal",
        },
        "headline": "SismoAI-Bericht",
        "summary": (
            "Statussummen: normal {normal}; Beobachtung {watch}; erhöht {elevated}; "
            "stark atypisch {atypical}. Betriebsbereite Regionen: {total}."
        ),
        "no_previous": "Dies ist das erste Update für den automatischen Vergleich.",
        "no_state_changes": "Seit dem vorherigen Update gab es keine Stufenänderungen.",
        "state_changes": "Stufenänderungen seit dem vorherigen Update: {items}.",
        "change_item": "{region} wechselte von {before} zu {after}",
        "region": "{region} ist {state}, mit vorläufigem IEDC {iedc} und {confidence} Konfidenz. {reason} {event}",
        "reason_count": "Die jüngste seismische Rate wich vom regionalen Referenzwert ab.",
        "reason_magnitude": "Die jüngste maximale Magnitude wich vom regionalen Referenzwert ab.",
        "reason_generic": "Die Berechnung erkannte eine statistische Abweichung im jüngsten Zeitfenster.",
        "event": "Das jüngste gelistete Ereignis hatte Magnitude {magnitude}, am {date}, bei {place}.",
        "no_notable": "Derzeit liegt keine Region über dem normalen Niveau.",
        "history": (
            "Das historische Labor hat seit 1973 {progress} verarbeitet: {events} USGS-Ereignisse ab "
            "Magnitude 4,5 und {patterns} Kandidatenmuster. Diese Muster dienen nur der Forschung."
        ),
        "gate_closed": (
            "Es gibt kein genehmigtes experimentelles Signal. Das öffentliche Gate ist geschlossen; "
            "dies ist keine Vorhersage, offizielle Warnung oder Evakuierungsanordnung."
        ),
        "signal": (
            "PRIORITÄRES EXPERIMENTELLES SIGNAL — KEINE OFFIZIELLE WARNUNG. {region}, Zeitfenster {window}, "
            "Ziel {target}, experimentelle Wahrscheinlichkeit {probability} gegenüber {baseline}. "
            "Beachten Sie stets die offiziellen Behörden."
        ),
        "candidate": "Das Labor führt {patterns} Kandidatenmuster getrennt vom Betriebssystem.",
        "latest": "Letztes Update", "changes": "Was sich geändert hat", "situation": "Aktuelle Lage",
        "limitations": "Umfang und Grenzen", "history_title": "Gedächtnis und Muster",
        "listen": "Bericht anhören", "pause": "Pause", "resume": "Fortsetzen", "stop": "Stoppen",
        "no_voice": "Auf diesem Gerät ist keine Stimme für die gewählte Sprache verfügbar.",
        "auto": "Automatisch", "archive": "Berichtsarchiv", "official": "Keine offizielle Warnung",
    },
    "ja": {
        "name": "日本語",
        "voice": "ja",
        "states": {
            "NORMAL": "通常", "WATCH": "監視", "ELEVATED": "上昇",
            "HIGHLY_ATYPICAL": "非常に異常", "NO_DATA": "データなし",
        },
        "classes": {
            "OBSERVED_ACTIVITY": "観測された活動", "STATISTICAL_ANOMALY": "統計的異常",
            "CANDIDATE_PATTERN": "候補パターン", "EXPERIMENTAL_SIGNAL": "優先実験シグナル",
        },
        "headline": "SismoAI速報",
        "summary": (
            "状態集計：通常{normal}、監視{watch}、上昇{elevated}、非常に異常{atypical}。"
            "稼働地域は{total}です。"
        ),
        "no_previous": "自動比較に利用できる最初の更新です。",
        "no_state_changes": "前回の更新からレベル変更はありません。",
        "state_changes": "前回の更新からのレベル変更：{items}。",
        "change_item": "{region}は{before}から{after}に変更",
        "region": "{region}は{state}で、暫定IEDCは{iedc}、信頼度は{confidence}です。{reason} {event}",
        "reason_count": "最近の地震発生率が地域基準から外れました。",
        "reason_magnitude": "最近の最大マグニチュードが地域基準から外れました。",
        "reason_generic": "最近の期間で統計的な偏差が検出されました。",
        "event": "最新の掲載イベントは{date}、{place}で、マグニチュード{magnitude}です。",
        "no_notable": "現在、通常レベルを超える地域はありません。",
        "history": (
            "履歴研究は1973年以降の{progress}を処理済みで、マグニチュード4.5以上のUSGSイベント"
            "{events}件と候補パターン{patterns}件があります。候補は研究専用です。"
        ),
        "gate_closed": (
            "承認された実験シグナルはありません。公開ゲートは閉じており、これは予測、"
            "公式警報、避難命令ではありません。"
        ),
        "signal": (
            "優先実験シグナル — 公式警報ではありません。地域{region}、期間{window}、"
            "対象{target}、実験確率{probability}、基準{baseline}。必ず公的機関を確認してください。"
        ),
        "candidate": "研究室は運用システムと分離して{patterns}件の候補パターンを保持しています。",
        "latest": "最終更新", "changes": "変更点", "situation": "現在の状況",
        "limitations": "範囲と制限", "history_title": "履歴とパターン",
        "listen": "速報を聞く", "pause": "一時停止", "resume": "再開", "stop": "停止",
        "no_voice": "選択した言語の音声がこの端末にありません。",
        "auto": "自動", "archive": "速報アーカイブ", "official": "公式警報ではありません",
    },
    "tr": {
        "name": "Türkçe",
        "voice": "tr",
        "states": {
            "NORMAL": "normal", "WATCH": "izlemede", "ELEVATED": "yüksek",
            "HIGHLY_ATYPICAL": "çok sıra dışı", "NO_DATA": "veri yok",
        },
        "classes": {
            "OBSERVED_ACTIVITY": "Gözlenen faaliyet", "STATISTICAL_ANOMALY": "İstatistiksel anomali",
            "CANDIDATE_PATTERN": "Aday örüntü", "EXPERIMENTAL_SIGNAL": "Öncelikli deneysel sinyal",
        },
        "headline": "SismoAI Bülteni",
        "summary": (
            "Durum toplamları: normal {normal}; izlemede {watch}; yüksek {elevated}; "
            "çok sıra dışı {atypical}. Çalışan bölge: {total}."
        ),
        "no_previous": "Bu, otomatik karşılaştırma için kullanılabilen ilk güncellemedir.",
        "no_state_changes": "Önceki güncellemeden bu yana seviye değişikliği olmadı.",
        "state_changes": "Önceki güncellemeden bu yana seviye değişiklikleri: {items}.",
        "change_item": "{region}, {before} seviyesinden {after} seviyesine geçti",
        "region": "{region} {state}; geçici IEDC {iedc}, güven {confidence}. {reason} {event}",
        "reason_count": "Son sismik oran bölgesel referanstan uzaklaştı.",
        "reason_magnitude": "Son en yüksek büyüklük bölgesel referanstan uzaklaştı.",
        "reason_generic": "Hesaplama son zaman penceresinde istatistiksel sapma belirledi.",
        "event": "Listelenen en son olay {date} tarihinde {place} konumunda, {magnitude} büyüklüğündedir.",
        "no_notable": "Şu anda hiçbir bölge normal seviyenin üzerinde değildir.",
        "history": (
            "Tarihsel laboratuvar 1973'ten bu yana {progress} işledi: 4,5 ve üzeri {events} USGS olayı "
            "ve {patterns} aday örüntü. Bu örüntüler yalnızca araştırma içindir."
        ),
        "gate_closed": (
            "Onaylanmış deneysel sinyal yoktur. Kamu kapısı kapalıdır; bu bir tahmin, resmi uyarı "
            "veya tahliye emri değildir."
        ),
        "signal": (
            "ÖNCELİKLİ DENEYSEL SİNYAL — RESMİ UYARI DEĞİLDİR. {region}, pencere {window}, hedef {target}, "
            "deneysel olasılık {probability}, referans {baseline}. Daima resmi makamları izleyin."
        ),
        "candidate": "Laboratuvar, işletim sisteminden ayrı {patterns} aday örüntü tutuyor.",
        "latest": "Son güncelleme", "changes": "Ne değişti", "situation": "Mevcut durum",
        "limitations": "Kapsam ve sınırlamalar", "history_title": "Hafıza ve örüntüler",
        "listen": "Bülteni dinle", "pause": "Duraklat", "resume": "Devam et", "stop": "Durdur",
        "no_voice": "Bu cihazda seçilen dil için ses bulunmuyor.",
        "auto": "Otomatik", "archive": "Bülten arşivi", "official": "Resmi uyarı değildir",
    },
    "el": {
        "name": "Ελληνικά",
        "voice": "el",
        "states": {
            "NORMAL": "κανονική", "WATCH": "υπό παρακολούθηση", "ELEVATED": "αυξημένη",
            "HIGHLY_ATYPICAL": "πολύ ασυνήθιστη", "NO_DATA": "χωρίς δεδομένα",
        },
        "classes": {
            "OBSERVED_ACTIVITY": "Παρατηρούμενη δραστηριότητα", "STATISTICAL_ANOMALY": "Στατιστική ανωμαλία",
            "CANDIDATE_PATTERN": "Υποψήφιο μοτίβο", "EXPERIMENTAL_SIGNAL": "Πειραματικό σήμα προτεραιότητας",
        },
        "headline": "Δελτίο SismoAI",
        "summary": (
            "Σύνολα κατάστασης: κανονική {normal}, παρακολούθηση {watch}, αυξημένη {elevated}, "
            "πολύ ασυνήθιστη {atypical}. Ενεργές περιοχές: {total}."
        ),
        "no_previous": "Αυτή είναι η πρώτη διαθέσιμη ενημέρωση για αυτόματη σύγκριση.",
        "no_state_changes": "Δεν υπήρξαν αλλαγές επιπέδου από την προηγούμενη ενημέρωση.",
        "state_changes": "Αλλαγές επιπέδου από την προηγούμενη ενημέρωση: {items}.",
        "change_item": "Η περιοχή {region} άλλαξε από {before} σε {after}",
        "region": "Η περιοχή {region} είναι {state}, με προσωρινό IEDC {iedc} και εμπιστοσύνη {confidence}. {reason} {event}",
        "reason_count": "Ο πρόσφατος σεισμικός ρυθμός αποκλίνει από την περιφερειακή αναφορά.",
        "reason_magnitude": "Το πρόσφατο μέγιστο μέγεθος αποκλίνει από την περιφερειακή αναφορά.",
        "reason_generic": "Ο υπολογισμός εντόπισε στατιστική απόκλιση στο πρόσφατο παράθυρο.",
        "event": "Το πιο πρόσφατο συμβάν είναι μεγέθους {magnitude}, στις {date}, στην περιοχή {place}.",
        "no_notable": "Καμία περιοχή δεν υπερβαίνει τώρα το κανονικό επίπεδο.",
        "history": (
            "Το ιστορικό εργαστήριο έχει επεξεργαστεί {progress} από το 1973: {events} συμβάντα USGS "
            "μεγέθους 4,5 ή μεγαλύτερα και {patterns} υποψήφια μοτίβα. Τα μοτίβα είναι μόνο για έρευνα."
        ),
        "gate_closed": (
            "Δεν υπάρχει εγκεκριμένο πειραματικό σήμα. Η δημόσια πύλη είναι κλειστή και αυτό δεν είναι "
            "πρόβλεψη, επίσημη προειδοποίηση ή εντολή εκκένωσης."
        ),
        "signal": (
            "ΠΕΙΡΑΜΑΤΙΚΟ ΣΗΜΑ ΠΡΟΤΕΡΑΙΟΤΗΤΑΣ — ΔΕΝ ΕΙΝΑΙ ΕΠΙΣΗΜΗ ΠΡΟΕΙΔΟΠΟΙΗΣΗ. {region}, "
            "παράθυρο {window}, στόχος {target}, πειραματική πιθανότητα {probability}, αναφορά {baseline}. "
            "Να συμβουλεύεστε πάντα τις επίσημες αρχές."
        ),
        "candidate": "Το εργαστήριο διατηρεί {patterns} υποψήφια μοτίβα χωριστά από το λειτουργικό σύστημα.",
        "latest": "Τελευταία ενημέρωση", "changes": "Τι άλλαξε", "situation": "Τρέχουσα κατάσταση",
        "limitations": "Εύρος και περιορισμοί", "history_title": "Μνήμη και μοτίβα",
        "listen": "Ακρόαση δελτίου", "pause": "Παύση", "resume": "Συνέχεια", "stop": "Διακοπή",
        "no_voice": "Η συσκευή δεν διαθέτει φωνή για την επιλεγμένη γλώσσα.",
        "auto": "Αυτόματα", "archive": "Αρχείο δελτίων", "official": "Δεν είναι επίσημη προειδοποίηση",
    },
    "id": {
        "name": "Bahasa Indonesia",
        "voice": "id",
        "states": {
            "NORMAL": "normal", "WATCH": "dalam pengamatan", "ELEVATED": "meningkat",
            "HIGHLY_ATYPICAL": "sangat tidak biasa", "NO_DATA": "tanpa data",
        },
        "classes": {
            "OBSERVED_ACTIVITY": "Aktivitas teramati", "STATISTICAL_ANOMALY": "Anomali statistik",
            "CANDIDATE_PATTERN": "Pola kandidat", "EXPERIMENTAL_SIGNAL": "Sinyal eksperimental prioritas",
        },
        "headline": "Buletin SismoAI",
        "summary": (
            "Jumlah status: normal {normal}; pengamatan {watch}; meningkat {elevated}; "
            "sangat tidak biasa {atypical}. Wilayah operasional: {total}."
        ),
        "no_previous": "Ini adalah pembaruan pertama yang tersedia untuk perbandingan otomatis.",
        "no_state_changes": "Tidak ada perubahan tingkat sejak pembaruan sebelumnya.",
        "state_changes": "Perubahan tingkat sejak pembaruan sebelumnya: {items}.",
        "change_item": "{region} berubah dari {before} menjadi {after}",
        "region": "{region} berada pada {state}, dengan IEDC sementara {iedc} dan keyakinan {confidence}. {reason} {event}",
        "reason_count": "Laju seismik terbaru menyimpang dari acuan regional.",
        "reason_magnitude": "Magnitudo maksimum terbaru menyimpang dari acuan regional.",
        "reason_generic": "Perhitungan mendeteksi penyimpangan statistik pada jendela terbaru.",
        "event": "Peristiwa terbaru yang tercantum bermagnitudo {magnitude}, pada {date}, di {place}.",
        "no_notable": "Saat ini tidak ada wilayah di atas tingkat normal.",
        "history": (
            "Laboratorium historis telah memproses {progress} sejak 1973: {events} peristiwa USGS "
            "bermagnitudo 4,5 atau lebih dan {patterns} pola kandidat. Pola tersebut hanya untuk penelitian."
        ),
        "gate_closed": (
            "Tidak ada sinyal eksperimental yang disetujui. Gerbang publik tertutup dan ini bukan "
            "prediksi, peringatan resmi, atau perintah evakuasi."
        ),
        "signal": (
            "SINYAL EKSPERIMENTAL PRIORITAS — BUKAN PERINGATAN RESMI. {region}, jendela {window}, "
            "target {target}, probabilitas eksperimental {probability} dibanding acuan {baseline}. "
            "Selalu ikuti otoritas resmi."
        ),
        "candidate": "Laboratorium menyimpan {patterns} pola kandidat secara terpisah dari sistem operasional.",
        "latest": "Pembaruan terakhir", "changes": "Apa yang berubah", "situation": "Situasi saat ini",
        "limitations": "Cakupan dan batasan", "history_title": "Memori dan pola",
        "listen": "Dengarkan buletin", "pause": "Jeda", "resume": "Lanjutkan", "stop": "Hentikan",
        "no_voice": "Perangkat ini tidak memiliki suara untuk bahasa yang dipilih.",
        "auto": "Otomatis", "archive": "Arsip buletin", "official": "Bukan peringatan resmi",
    },
}


SEVERITY = {
    "NO_DATA": -1,
    "NORMAL": 0,
    "WATCH": 1,
    "ELEVATED": 2,
    "HIGHLY_ATYPICAL": 3,
}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def _previous_world(directory: Path | None, generated_at: str) -> dict[str, Any] | None:
    if not directory or not directory.exists():
        return None
    candidates: list[tuple[str, dict[str, Any]]] = []
    for path in directory.glob("*.json"):
        item = _read_json(path)
        if not item:
            continue
        stamp = str(item.get("generated_at") or "")
        if stamp and stamp < generated_at:
            candidates.append((stamp, item))
    return max(candidates, key=lambda x: x[0])[1] if candidates else None


def _percent(value: Any, decimals: int = 0) -> str:
    return f"{max(0.0, min(1.0, float(value or 0))) * 100:.{decimals}f}%"


def _number(value: Any, decimals: int = 1) -> str:
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return "—"


def _reason_key(reasons: list[dict[str, Any]]) -> str:
    features = {str(x.get("feature") or "") for x in reasons}
    if "seismic_count" in features:
        return "reason_count"
    if "seismic_max_mag" in features:
        return "reason_magnitude"
    return "reason_generic"


def _strict_signal(region: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    signal = region.get("signal") if isinstance(region.get("signal"), dict) else {}
    requirements = {
        "public_gate": bool(region.get("public_valid")),
        "baseline_complete": float(region.get("baseline_progress") or 0) >= 1.0,
        "three_independent_families": int(region.get("available_families") or 0) >= 3,
        "confidence_75": float(region.get("confidence") or 0) >= 0.75,
        "prospective_validation": bool(signal.get("prospective_validation")),
        "probability_calibrated": bool(signal.get("probability_calibrated")),
        "acceptable_false_alarm_rate": bool(signal.get("acceptable_false_alarm_rate")),
        "complete_message_fields": all(
            signal.get(key) not in {None, ""}
            for key in ("window", "target", "probability", "baseline_probability")
        ),
    }
    return all(requirements.values()), requirements


def _localized_messages(
    *,
    language: str,
    classification: str,
    generated_at: str,
    counts: Counter,
    total: int,
    changes: list[dict[str, Any]],
    notable: list[dict[str, Any]],
    historical: dict[str, Any],
    approved_signal: dict[str, Any] | None,
) -> dict[str, Any]:
    t = LANGUAGES[language]
    states = t["states"]
    summary = t["summary"].format(
        normal=counts["NORMAL"],
        watch=counts["WATCH"],
        elevated=counts["ELEVATED"],
        atypical=counts["HIGHLY_ATYPICAL"],
        total=total,
    )
    if changes:
        items = "; ".join(
            t["change_item"].format(
                region=x["region_name"],
                before=states.get(x["before"], x["before"]),
                after=states.get(x["after"], x["after"]),
            )
            for x in changes[:6]
        )
        changes_text = t["state_changes"].format(items=items)
    elif changes is not None:
        changes_text = t["no_state_changes"]
    else:
        changes_text = t["no_previous"]

    region_texts: list[str] = []
    for region in notable[:5]:
        latest = region.get("latest_event") or {}
        event_text = ""
        if latest.get("magnitude") is not None:
            event_text = t["event"].format(
                magnitude=_number(latest.get("magnitude"), 1),
                date=str(latest.get("event_time") or "")[:10] or "—",
                place=str(latest.get("place") or "—"),
            )
        region_texts.append(
            t["region"].format(
                region=region["region_name"],
                state=states.get(region["state"], region["state"]),
                iedc=_number(region.get("iedc_provisional")),
                confidence=_percent(region.get("confidence")),
                reason=t[_reason_key(region.get("reasons") or [])],
                event=event_text,
            ).strip()
        )
    if not region_texts:
        region_texts = [t["no_notable"]]

    catalog = historical.get("catalog") if isinstance(historical.get("catalog"), dict) else {}
    patterns = historical.get("patterns") if isinstance(historical.get("patterns"), list) else []
    history_text = t["history"].format(
        progress=_percent(catalog.get("progress"), 1),
        events=f"{int(catalog.get('events') or 0):,}",
        patterns=len(patterns),
    )
    if approved_signal:
        s = approved_signal.get("signal") or {}
        limitations = t["signal"].format(
            region=approved_signal["region_name"],
            window=s.get("window"),
            target=s.get("target"),
            probability=_percent(s.get("probability")),
            baseline=_percent(s.get("baseline_probability")),
        )
    else:
        limitations = t["gate_closed"]

    class_label = t["classes"][classification]
    spoken = " ".join(
        [t["headline"] + ". " + class_label + ".", summary, changes_text]
        + region_texts[:3]
        + [history_text, limitations]
    )
    return {
        "language": language,
        "language_name": t["name"],
        "voice_prefix": t["voice"],
        "headline": t["headline"],
        "classification": class_label,
        "generated_label": t["latest"],
        "changes_label": t["changes"],
        "situation_label": t["situation"],
        "limitations_label": t["limitations"],
        "history_label": t["history_title"],
        "listen_label": t["listen"],
        "pause_label": t["pause"],
        "resume_label": t["resume"],
        "stop_label": t["stop"],
        "no_voice": t["no_voice"],
        "automatic_label": t["auto"],
        "archive_label": t["archive"],
        "official_label": t["official"],
        "summary": summary,
        "changes": changes_text,
        "regions": region_texts,
        "historical": history_text,
        "limitations": limitations,
        "spoken": spoken,
        "generated_at": generated_at,
    }


def build_bulletin(
    *,
    world: dict[str, Any],
    historical: dict[str, Any] | None = None,
    previous_world_dir: Path | None = None,
) -> dict[str, Any]:
    generated_at = str(
        world.get("generated_at")
        or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    ranking = [x for x in (world.get("ranking") or []) if isinstance(x, dict)]
    counts = Counter(str(x.get("state") or "NO_DATA") for x in ranking)
    previous = _previous_world(previous_world_dir, generated_at)
    previous_map = {
        str(x.get("region_id")): x
        for x in ((previous or {}).get("ranking") or [])
        if isinstance(x, dict) and x.get("region_id")
    }
    changes: list[dict[str, Any]] | None = [] if previous else None
    numeric_changes: list[dict[str, Any]] = []
    if previous:
        for region in ranking:
            before = previous_map.get(str(region.get("region_id")))
            if not before:
                continue
            old_state = str(before.get("state") or "NO_DATA")
            new_state = str(region.get("state") or "NO_DATA")
            old_iedc = float(before.get("iedc_provisional") or 0)
            new_iedc = float(region.get("iedc_provisional") or 0)
            delta = new_iedc - old_iedc
            numeric_changes.append({
                "region_id": region.get("region_id"),
                "region_name": region.get("region_name"),
                "before": old_iedc,
                "after": new_iedc,
                "delta": round(delta, 3),
            })
            if old_state != new_state:
                changes.append({
                    "region_id": region.get("region_id"),
                    "region_name": region.get("region_name"),
                    "before": old_state,
                    "after": new_state,
                    "iedc_before": old_iedc,
                    "iedc_after": new_iedc,
                })
        changes.sort(
            key=lambda x: (
                SEVERITY.get(str(x["after"]), -1) - SEVERITY.get(str(x["before"]), -1),
                float(x["iedc_after"]) - float(x["iedc_before"]),
            ),
            reverse=True,
        )
        numeric_changes.sort(key=lambda x: abs(float(x["delta"])), reverse=True)

    notable = [
        x for x in ranking
        if str(x.get("state") or "NO_DATA") in {"WATCH", "ELEVATED", "HIGHLY_ATYPICAL"}
    ]
    notable.sort(
        key=lambda x: (
            SEVERITY.get(str(x.get("state")), -1),
            float(x.get("iedc_provisional") or 0),
        ),
        reverse=True,
    )

    signal_requirements: list[dict[str, Any]] = []
    approved_signal = None
    for region in ranking:
        approved, requirements = _strict_signal(region)
        signal_requirements.append({
            "region_id": region.get("region_id"),
            "approved": approved,
            "requirements": requirements,
        })
        if approved and approved_signal is None:
            approved_signal = region

    historical = historical or {}
    patterns = historical.get("patterns") if isinstance(historical.get("patterns"), list) else []
    if approved_signal:
        classification = "EXPERIMENTAL_SIGNAL"
    elif notable:
        classification = "STATISTICAL_ANOMALY"
    elif patterns:
        classification = "OBSERVED_ACTIVITY"
    else:
        classification = "OBSERVED_ACTIVITY"

    messages = {
        code: _localized_messages(
            language=code,
            classification=classification,
            generated_at=generated_at,
            counts=counts,
            total=int(world.get("regions_operational") or len(ranking)),
            changes=changes,
            notable=notable,
            historical=historical,
            approved_signal=approved_signal,
        )
        for code in LANGUAGES
    }
    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "previous_generated_at": (previous or {}).get("generated_at"),
        "classification": classification,
        "official_alert": False,
        "public_gate_approved_regions": int(world.get("regions_public_valid") or 0),
        "regions_operational": int(world.get("regions_operational") or len(ranking)),
        "state_counts": {
            state: counts[state]
            for state in ("NORMAL", "WATCH", "ELEVATED", "HIGHLY_ATYPICAL", "NO_DATA")
        },
        "state_changes": changes,
        "largest_iedc_changes": numeric_changes[:8],
        "notable_regions": [
            {
                key: region.get(key)
                for key in (
                    "region_id", "region_name", "state", "iedc_provisional", "confidence",
                    "coverage", "data_quality", "baseline_progress", "available_families",
                    "reasons", "latest_event", "public_valid", "signal",
                )
            }
            for region in notable[:8]
        ],
        "candidate_patterns": len(patterns),
        "historical_progress": (historical.get("catalog") or {}).get("progress", 0),
        "signal_gate": {
            "approved": bool(approved_signal),
            "official_alert": False,
            "policy": (
                "Requires public gate, complete baseline, at least three independent families, "
                "confidence >= 75%, prospective validation, calibrated probability, acceptable false-alarm "
                "rate and complete signal fields."
            ),
            "checks": signal_requirements,
        },
        "languages": [
            {"code": code, "name": value["name"], "voice_prefix": value["voice"]}
            for code, value in LANGUAGES.items()
        ],
        "messages": messages,
    }


def selftest() -> dict[str, Any]:
    sample_previous = {
        "generated_at": "2026-01-01T00:00:00Z",
        "ranking": [{
            "region_id": "sample", "region_name": "Región de prueba", "state": "NORMAL",
            "iedc_provisional": 10,
        }],
    }
    sample_world = {
        "generated_at": "2026-01-02T00:00:00Z",
        "regions_operational": 1,
        "regions_public_valid": 0,
        "ranking": [{
            "region_id": "sample", "region_name": "Región de prueba", "state": "WATCH",
            "iedc_provisional": 35, "confidence": .45, "coverage": .5, "data_quality": .95,
            "baseline_progress": 1, "available_families": 1, "public_valid": False,
            "reasons": [{"feature": "seismic_count"}],
            "latest_event": {"magnitude": 4.5, "event_time": "2026-01-01T12:00:00Z", "place": "Prueba"},
        }],
    }
    import tempfile
    with tempfile.TemporaryDirectory(prefix="sismoai_bulletin_selftest_") as temporary:
        directory = Path(temporary)
        (directory / "previous.json").write_text(
            json.dumps(sample_previous, ensure_ascii=False), encoding="utf-8"
        )
        result = build_bulletin(
            world=sample_world,
            historical={"catalog": {"progress": .1, "events": 100}, "patterns": [{}]},
            previous_world_dir=directory,
        )
        signal_world = json.loads(json.dumps(sample_world))
        signal_world["regions_public_valid"] = 1
        signal_region = signal_world["ranking"][0]
        signal_region.update({
            "public_valid": True,
            "baseline_progress": 1,
            "available_families": 3,
            "confidence": .8,
            "signal": {
                "prospective_validation": True,
                "probability_calibrated": True,
                "acceptable_false_alarm_rate": True,
                "window": "7 days",
                "target": "M≥5",
                "probability": .2,
                "baseline_probability": .05,
            },
        })
        signal_result = build_bulletin(
            world=signal_world,
            historical={"catalog": {"progress": 1, "events": 1000}, "patterns": [{}]},
            previous_world_dir=directory,
        )
    checks = {
        "classification": result["classification"] == "STATISTICAL_ANOMALY",
        "state_change": len(result["state_changes"] or []) == 1,
        "strict_gate_closed": not result["signal_gate"]["approved"],
        "never_official": result["official_alert"] is False,
        "languages": len(result["messages"]) == len(LANGUAGES) >= 10,
        "spanish": "No existe una señal" in result["messages"]["es"]["limitations"],
        "english": "not a prediction" in result["messages"]["en"]["limitations"],
        "spoken": all(x.get("spoken") for x in result["messages"].values()),
        "strict_gate_opens": signal_result["classification"] == "EXPERIMENTAL_SIGNAL",
        "signal_still_not_official": signal_result["official_alert"] is False,
        "signal_wording": "NO ES ALERTA OFICIAL" in signal_result["messages"]["es"]["limitations"],
    }
    return {"status": "OK" if all(checks.values()) else "FAILED", "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SismoAI deterministic multilingual bulletin")
    parser.add_argument("command", choices=["selftest"])
    args = parser.parse_args(argv)
    if args.command == "selftest":
        result = selftest()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "OK" else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
