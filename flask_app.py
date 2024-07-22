from requests_html import HTMLSession, AsyncHTMLSession
from flask import Flask, render_template, request
from urllib.parse import urlsplit
from io import BytesIO
import pdfplumber
import traceback
import pyppeteer
import requests
import time
import re

app = Flask(__name__)

def checkTable(table):
    count = 0

    for i in table:
        count += 1

        if i[0] and (i[0].lower().find("homepage") != -1 or i[0].lower().find("home:") != -1):
            return table[count - 1:]
    return False

class AsyncHTMLSessionFixed(AsyncHTMLSession):
    def __init__(self, **kwargs):
        super(AsyncHTMLSessionFixed, self).__init__(**kwargs)
        self.__browser_args = kwargs.get("browser_args", ["--no-sandbox"])

    @property
    async def browser(self):
        if not hasattr(self, "_browser"):
            self._browser = await pyppeteer.launch(ignoreHTTPSErrors=not(self.verify), headless=True, handleSIGINT=False, handleSIGTERM=False, handleSIGHUP=False, args=self.__browser_args)

        return self._browser

@app.route("/", methods=["GET", "POST"])
async def index():
    if request.method == "GET":
        return render_template("index.html")
    elif request.method == "POST":
        startTime = time.time()
        totalTime = time.time()
        ID = request.form["ID"]
        print(f"ID: {ID}")

        #Scrape website
        try:
            #Get website HTML
            session = HTMLSession()
            html = session.get(f"https://www.toegankelijkheidsverklaring.nl/register/{ID}").html
            session.close()

            #Check if ID is valid
            if html.find("title", first=True).text.find("Pagina niet gevonden") != -1:
                return render_template("index.html", ID=ID, error="ERROR: ID NOT FOUND")

            #Scrape website data
            infodiv = html.find("#samenvatting", first=True)
            status = infodiv.find("strong", first=True).text

            lastChangeDate = infodiv.find("p")[2].text[-10:]

            try:
                annotation = infodiv.find("strong")[1].text
            except:
                annotation = None

            domaindiv = html.find("#verklaring", first=True)
            domain = domaindiv.find("ul", first=True).find("li")[1].text

            subdomainElements = []
            for subdomain in domaindiv.find("ul")[2].find("li"):
                subdomainElements.append(subdomain.text)
            subdomains = ", ".join(subdomainElements)

            contactInfo = ", ".join(re.findall("[A-Za-z0-9\.\-]+@[A-Za-z0-9\.\-]+\.[A-Za-z]+", domaindiv.text))

            try:
                extrainfo = ""
                elements = domaindiv.find("*")
                elementCount = 0
                tags = ["p", "li"]

                for element in elements:
                    elementCount += 1

                    if "id" in element.attrs and element.attrs["id"] == "verklaring-aanvullende-informatie":
                        for i in elements[elementCount:]:
                            if i.tag in tags:
                                extrainfo += i.text + "<br>"
                        break
            except:
                extrainfo = None

            #Check if audit was accepted
            if status[0] == "A" or status[0] == "B":
                researchList = html.find("#onderzoeksresultaten", first=True)

                #Get dates
                reportDates = []

                for i in researchList.find("ul")[1:]:
                    reportDates.append(i.find("li", first=True).text)
                
                #Get reports
                reports = []

                for i in researchList.find("[itemprop=onderzoeksresultaat-url]"):
                    report = i.attrs["href"]
                    reports.append(report)
            else:
                return render_template("index.html", 
                    ID=ID,
                    time=round(time.time() - totalTime, 2),
                    status=status,
                    annotation=annotation,
                    last_change_date=lastChangeDate,
                    main_url=domain,
                    other_url=subdomains,
                    contact_info=contactInfo,
                    extra_info=extrainfo,
                )
        except Exception as e:
            print(traceback.format_exc())
            return render_template("index.html", ID=ID, error=f"WEBSITE ERROR: {e}")
        print(f"WEBSITE TIME: {round(time.time() - startTime, 2)}")
        startTime = time.time()

        #Scrape spreadsheet
        try:
            #Get spreadsheet HTML
            session = HTMLSession()
            html = session.get("https://docs.google.com/spreadsheets/d/1yE808toaQtptkHsUFzqa4MYZNig6KpjbFmZRdYhe4xA/gviz/tq?tqx=out:html&tq&gid=1").html
            session.close()

            #Scrape spreadsheet data
            rowSearch = re.search(rf".+{ID}</td>", str(html.html))

            if rowSearch:
                columns = re.findall(r"<td>[^<>]+</td>", rowSearch.group())

            urlStatement = columns[12][4:-5] if rowSearch else None

            issuesStatement = columns[7][4:-5] if rowSearch else None

            lastContactDate = columns[8][4:-5] if rowSearch else None
        except Exception as e:
            print(traceback.format_exc())
            return render_template("index.html", ID=ID, error=f"SPREADSHEET ERROR: {e}")
        print(f"SPREADSHEET TIME: {round(time.time() - startTime, 2)}")
        startTime = time.time()

        #Scrape reports
        try:
            reportVars1 = {}
            reportVars2 = {}
            reportCount = 0

            for report in reports:
                reportCount += 1
                reportText = ""

                #Get report HTML
                session = AsyncHTMLSessionFixed()
                response = await session.get(report)
                html = response.html
                await session.close()

                #Check if report is a PDF
                contentType = response.headers.get("Content-Type")

                if contentType.find("application/pdf") != -1 or report.find("/documenten/") != -1:
                    #PDF download version
                    if contentType.find("application/pdf") == -1 and report.find("/documenten/") != -1:
                        #Find PDF download link
                        for a in html.find("a"):
                            href = a.attrs["href"] or None

                            if href and href.find(".pdf") != -1:
                                if href.find("https://") == -1:
                                    href = f"https://{urlsplit(report).netloc}{href}"
                                download = requests.get(href)
                                break
                    else:
                        download = requests.get(report)

                    #PDF version
                    file = BytesIO(download.content)
                    pdf = pdfplumber.open(file)
                    sampleTable = []

                    #Get all PDF pages
                    for page in pdf.pages:
                        reportText += page.dedupe_chars(tolerance=1).extract_text()
                        table = page.dedupe_chars(tolerance=1).extract_table()

                        if table:
                            tableCheck = checkTable(table)

                            if tableCheck:
                                sampleTable = tableCheck

                    #Get scope
                    scopeSearch = re.search("(?s)(Scope van het onderzoek(:)?\n.*Steekproef(?!\n•)|Scope(:)?\n.*(Grootte va|Conformite))", reportText).group()
                    locals()[f"reportVars{reportCount}"]["scope"] = re.sub(r"\d\d•", "•", re.sub("(Scope van het onderzoek|Scope)(:)?\n", "", scopeSearch[:-11], count=1)).replace("\n", "<br>")

                    #Get samples
                    if sampleTable:
                        locals()[f"reportVars{reportCount}"]["samples"] = ""

                        for row in sampleTable:
                            locals()[f"reportVars{reportCount}"]["samples"] += f'<b>{row[0]}{": " if row[0].find(":") == -1 else " "}</b>{(row[1] if row[1] else "-")}<br>'.replace("\n", "")
                    else:
                        sampleSearch = re.search("(?s)(?:Volledige steekproef\n|Steekproef(:)?\n(?:De|•)).*\n(?:Cardan|Gebrui)", reportText).group()
                        locals()[f"reportVars{reportCount}"]["samples"] = re.sub(r"<br>(?!•)", "", re.sub(r"\d\d•", "•", sampleSearch[re.search("•", sampleSearch).start() - 1:-7]).replace("\n", "<br>"))

                    pdf.close()
                else:
                    #Check if report website is supported
                    if report.find("toegankelijkheidsrapport.swink.nl") != -1:
                        #Get scope
                        scopeTable = html.find("tbody", first=True)
                        scopeRows = scopeTable.find("th")
                        scopeData = scopeTable.find("td")
                        locals()[f"reportVars{reportCount}"]["scope"] = ""

                        for i in range(1, 5):
                            locals()[f"reportVars{reportCount}"]["scope"] += f"<b>{scopeRows[i - 1].text}: </b>{scopeData[i - 1].text}<br>"

                        #Get samples
                        urlBreaks = html.find(".urlBreak")
                        samples = urlBreaks[-1].find("li")
                        locals()[f"reportVars{reportCount}"]["samples"] = ""

                        for sample in samples:
                            locals()[f"reportVars{reportCount}"]["samples"] += f"{sample.text}<br>"
                    elif report.find("aenoprovincies.nl") != -1:
                        #Get scope
                        scopeData = html.find("dl")[1].find("*")[2:]
                        locals()[f"reportVars{reportCount}"]["scope"] = ""

                        for i in range(0, len(scopeData), 2):
                            locals()[f"reportVars{reportCount}"]["scope"] += f"<b>{scopeData[i].text}: </b>{scopeData[i + 1].text}<br>"

                        #Get samples
                        samples = html.find("ol", first=True).find("li")
                        locals()[f"reportVars{reportCount}"]["samples"] = ""

                        for sample in samples:
                            span = sample.find("span")
                            locals()[f"reportVars{reportCount}"]["samples"] += f"<b>{span[0].text}: </b>{span[1].text}<br>"
                    elif report.find("wcag.nl") != -1 and report.find("audit.wcag.nl") == -1:
                        if report.find("rapporten.wcag.nl") != -1:
                            await response.html.arender(sleep=1, timeout=100)

                        #Get scope
                        scopeData = html.find(".content__inner")[1].find("li")
                        locals()[f"reportVars{reportCount}"]["scope"] = ""

                        for i in scopeData:
                            locals()[f"reportVars{reportCount}"]["scope"] += f"{i.text}<br>"
                        
                        #Get samples
                        if report.find("rapporten.wcag.nl") != -1:
                            samples = html.find(".container.space-content")
                            locals()[f"reportVars{reportCount}"]["samples"] = samples[-4].text[11:].replace("Opent in een nieuw tabblad\n", "").replace("\n", "<br>")[:-26]
                        else:
                            samples = html.find("ul")[-5].find("li")
                            locals()[f"reportVars{reportCount}"]["samples"] = ""

                            for sample in samples:
                                locals()[f"reportVars{reportCount}"]["samples"] += f"{sample.text}<br>"
                    else:
                        locals()[f"reportVars{reportCount}"]["scope"] = "UNSUPPORTED WEBSITE"
                        locals()[f"reportVars{reportCount}"]["samples"] = "UNSUPPORTED WEBSITE"
                    reportText = html.text
                
                #Find search terms
                locals()[f"reportVars{reportCount}"]["searches"] = []
                searchItems = [
                        "wcag-em",
                        "basisniveau van (toegankelijkheidsondersteuning|toegankelijkheid)",
                        "gebruikte technologieën",
                        "gebruikte browsers en softwareprogramma"
                    ]

                for item in searchItems:
                    locals()[f"reportVars{reportCount}"]["searches"].append("Present" if re.search(item, reportText, re.IGNORECASE) else "Absent")
        except Exception as e:
            print(traceback.format_exc())
            return render_template("index.html",
                error=f"REPORT ERROR: {e}",
                ID=ID,
                time=round(time.time() - totalTime, 2),
                status=status,
                annotation=annotation,
                last_change_date=lastChangeDate,
                main_url=domain,
                other_url=subdomains,
                contact_info=contactInfo,
                extra_info=extrainfo,
                report1=reports[0],
                report_date1=reportDates[0][6:],
                report2=reports[1] if len(reports) > 1 else None,
                report_date2=reportDates[1][6:] if len(reports) > 1 else None,
                url_statement=urlStatement,
                issues_statement=issuesStatement,
                contact_date=lastContactDate
            )
        print(f"REPORT TIME: {round(time.time() - startTime, 2)}")
        print(f"TOTAL TIME: {round(time.time() - totalTime, 2)}")

        return render_template("index.html",
            ID=ID,
            time=round(time.time() - totalTime, 2),
            status=status,
            annotation=annotation,
            last_change_date=lastChangeDate,
            main_url=domain,
            other_url=subdomains,
            contact_info=contactInfo,
            extra_info=extrainfo,
            report1=reports[0],
            report_date1=reportDates[0][6:],
            report2=reports[1] if len(reports) > 1 else None,
            report_date2=reportDates[1][6:] if len(reports) > 1 else None,
            url_statement=urlStatement,
            issues_statement=issuesStatement,
            contact_date=lastContactDate,
            scope1=reportVars1["scope"],
            samples1=re.sub(r"https://[^<>•]+", r"<a href='\g<0>' target='_blank'>\g<0></a>", reportVars1["samples"]),
            search1_1=reportVars1["searches"][0],
            search1_2=reportVars1["searches"][1],
            search1_3=reportVars1["searches"][2],
            search1_4=reportVars1["searches"][3],
            scope2=reportVars2["scope"] if len(reports) > 1 else None,
            samples2=re.sub(r"https://[^<>•]+", r"<a href='\g<0>' target='_blank'>\g<0></a>", reportVars2["samples"]) if len(reports) > 1 else None,
            search2_1=reportVars2["searches"][0] if len(reports) > 1 else None,
            search2_2=reportVars2["searches"][1] if len(reports) > 1 else None,
            search2_3=reportVars2["searches"][2] if len(reports) > 1 else None,
            search2_4=reportVars2["searches"][3] if len(reports) > 1 else None
        )

if __name__ == "__main__":
    app.run()