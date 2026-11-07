'#Language "WWB-COM"
Attribute VB_Name = "CST_Export_Package"
Option Explicit

' Export a CST Studio Suite project as a VisualopenEMS CST package.
'
' The package contains:
'   manifest.json            units, frequency, boundaries, materials, ports
'   stl/<component>__<solid>.stl
'
' Run this macro with the CST 3D project open.  STL coordinates are written in
' metres (CST's native STL convention); the manifest declares that unit.
'
' This macro is deliberately conservative: features that cannot be queried
' reliably are reported as warnings in the manifest and are not guessed.

Private Const PACKAGE_FORMAT As String = "cst-export-v1"

Public Sub Main()
    Dim projPath As String
    Dim projectName As String
    Dim outputDir As String
    Dim stlDir As String
    Dim manifestPath As String

    ' In CST macros the project helpers are global functions, not members of
    ' a "Project" object (the VBA help groups them under that caption).
    projPath = GetProjectPath("Project")
    If Len(projPath) = 0 Then
        MsgBox "Bitte zuerst ein CST-Projekt oeffnen.", vbExclamation
        Exit Sub
    End If
    projectName = BaseName(StripCstExtension(projPath))

    outputDir = InputBox( _
        "Zielordner fuer das CST-Exportpaket:" & vbCrLf & _
        "(manifest.json + stl/ mit einer STL pro Solid)", _
        "CST Exportpaket", _
        StripCstExtension(projPath) & "_cst_package")
    If Len(Trim$(outputDir)) = 0 Then Exit Sub
    outputDir = Trim$(outputDir)

    If Not FolderExists(outputDir) Then
        On Error Resume Next
        MkDir outputDir
        If Err.Number <> 0 Then
            MsgBox "Ordner konnte nicht erstellt werden:" & vbCrLf & outputDir & _
                   vbCrLf & Err.Description, vbCritical
            Err.Clear
            On Error GoTo 0
            Exit Sub
        End If
        On Error GoTo 0
    End If

    stlDir = outputDir & "\stl"
    If Not FolderExists(stlDir) Then
        On Error Resume Next
        MkDir stlDir
        If Err.Number <> 0 Then
            MsgBox "Unterordner 'stl' konnte nicht erstellt werden:" & vbCrLf & _
                   stlDir & vbCrLf & Err.Description, vbCritical
            Err.Clear
            On Error GoTo 0
            Exit Sub
        End If
        On Error GoTo 0
    End If

    ' A dedicated package folder can be refreshed safely.
    ClearStlFolder stlDir

    Dim warnItems As String
    Dim matItems As String
    Dim objItems As String
    Dim portItems As String
    Dim objectCount As Long
    Dim materialCount As Long
    Dim portCount As Long

    warnItems = ""
    matItems = ""
    objItems = ""
    portItems = ""

    ' --- materials ------------------------------------------------------
    materialCount = CollectMaterials(matItems, warnItems)

    ' --- solids -> one STL per shape -----------------------------------
    objectCount = CollectSolids(stlDir, objItems, warnItems)

    ' --- ports ----------------------------------------------------------
    portCount = CollectPorts(portItems, warnItems)

    ' --- frequency / boundaries / units --------------------------------
    Dim gUnit As String
    Dim fUnit As String
    Dim gToSI As Double
    Dim fmin As Double
    Dim fmax As Double
    Dim haveFrequency As Boolean

    gUnit = Units.GetGeometryUnit
    fUnit = Units.GetFrequencyUnit
    gToSI = Units.GetGeometryUnitToSI

    fmin = 0#
    fmax = 0#
    haveFrequency = False
    On Error Resume Next
    fmin = Solver.GetFmin
    fmax = Solver.GetFmax
    If Err.Number = 0 And fmax > 0# Then haveFrequency = True
    Err.Clear
    On Error GoTo 0

    Dim bXmin As String, bXmax As String
    Dim bYmin As String, bYmax As String
    Dim bZmin As String, bZmax As String
    bXmin = Boundary.GetXmin
    bXmax = Boundary.GetXmax
    bYmin = Boundary.GetYmin
    bYmax = Boundary.GetYmax
    bZmin = Boundary.GetZmin
    bZmax = Boundary.GetZmax

    ' The CST calculation box is essential for PEC boundaries that act as a
    ' ground plane (e.g. a microstrip without an explicit ground layer).  Export
    ' the exact wall positions, not just the boundary type; the importer turns
    ' them into per-side air margins.
    Dim cXmin As Double, cXmax As Double
    Dim cYmin As Double, cYmax As Double
    Dim cZmin As Double, cZmax As Double
    Dim sXmin As Double, sXmax As Double
    Dim sYmin As Double, sYmax As Double
    Dim sZmin As Double, sZmax As Double
    Dim haveDomain As Boolean
    haveDomain = False
    On Error Resume Next
    Call Boundary.GetCalculationBox(cXmin, cXmax, cYmin, cYmax, cZmin, cZmax)
    Call Boundary.GetStructureBox(sXmin, sXmax, sYmin, sYmax, sZmin, sZmax)
    If Err.Number = 0 Then
        haveDomain = True
    Else
        warnItems = AddItem(warnItems, JStr( _
            "CST calculation/structure box could not be queried: " & Err.Description))
        Err.Clear
    End If
    On Error GoTo 0

    If haveDomain Then
        If cXmax <= cXmin Or cYmax <= cYmin Or cZmax <= cZmin Then
            haveDomain = False
            warnItems = AddItem(warnItems, JStr( _
                "CST calculation box is empty/invalid; the project air margin will be kept."))
        End If
    End If

    If LCase$(bXmin) = "pml" Or LCase$(bXmax) = "pml" Or _
       LCase$(bYmin) = "pml" Or LCase$(bYmax) = "pml" Or _
       LCase$(bZmin) = "pml" Or LCase$(bZmax) = "pml" Then
        warnItems = AddItem(warnItems, JStr( _
            "CST PML layer count is not queried by this macro; the import uses " & _
            "the project's current PML size (default 8)."))
    End If

    Dim json As String
    json = "{"
    json = json & JStr("format") & ":" & JStr(PACKAGE_FORMAT) & ","
    json = json & JStr("generator") & ":" & JStr("VisualopenEMS CST export macro") & ","
    json = json & JStr("project") & ":" & JStr(projectName) & ","
    json = json & JStr("units") & ":{" & _
           JStr("geometry") & ":" & JStr("m") & "," & _
           JStr("frequency") & ":" & JStr(fUnit) & "},"

    If haveFrequency Then
        json = json & JStr("frequency") & ":{" & _
               JStr("start") & ":" & JNum(fmin) & "," & _
               JStr("stop") & ":" & JNum(fmax) & "," & _
               JStr("unit") & ":" & JStr(fUnit) & "},"
    End If

    json = json & JStr("boundaries") & ":{" & _
           JStr("xmin") & ":" & JStr(bXmin) & "," & _
           JStr("xmax") & ":" & JStr(bXmax) & "," & _
           JStr("ymin") & ":" & JStr(bYmin) & "," & _
           JStr("ymax") & ":" & JStr(bYmax) & "," & _
           JStr("zmin") & ":" & JStr(bZmin) & "," & _
           JStr("zmax") & ":" & JStr(bZmax) & "},"

    If haveDomain Then
        ' Convert the CST project units to metres, matching the STL export.
        json = json & JStr("domain") & ":{" & _
               JStr("calculation_box") & ":{" & _
               JStr("min") & ":" & JVec3(cXmin * gToSI, cYmin * gToSI, cZmin * gToSI) & "," & _
               JStr("max") & ":" & JVec3(cXmax * gToSI, cYmax * gToSI, cZmax * gToSI) & "}," & _
               JStr("structure_box") & ":{" & _
               JStr("min") & ":" & JVec3(sXmin * gToSI, sYmin * gToSI, sZmin * gToSI) & "," & _
               JStr("max") & ":" & JVec3(sXmax * gToSI, sYmax * gToSI, sZmax * gToSI) & "}}," 
    End If

    json = json & JStr("materials") & ":[" & matItems & "],"
    json = json & JStr("objects") & ":[" & objItems & "],"
    json = json & JStr("ports") & ":[" & portItems & "],"
    json = json & JStr("warnings") & ":[" & warnItems & "]"
    json = json & "}"

    manifestPath = outputDir & "\manifest.json"
    If Not WriteTextFile(manifestPath, json) Then
        MsgBox "manifest.json konnte nicht geschrieben werden:" & vbCrLf & _
               manifestPath, vbCritical
        Exit Sub
    End If

    MsgBox "CST-Exportpaket geschrieben:" & vbCrLf & outputDir & vbCrLf & vbCrLf & _
           "Objekte: " & CStr(objectCount) & vbCrLf & _
           "Materialien: " & CStr(materialCount) & vbCrLf & _
           "Ports: " & CStr(portCount) & vbCrLf & vbCrLf & _
           "Jetzt in VisualopenEMS: File > Import CST Studio Package...", _
           vbInformation, "CST Exportpaket"
End Sub

' ---------------------------------------------------------------------------
' Materials
' ---------------------------------------------------------------------------
Private Function CollectMaterials(ByRef items As String, ByRef warnings As String) As Long
    Dim total As Long
    Dim i As Long
    Dim name As String
    Dim kind As String
    Dim cstType As String
    Dim epsX As Double, epsY As Double, epsZ As Double
    Dim muX As Double, muY As Double, muZ As Double
    Dim sigX As Double, sigY As Double, sigZ As Double
    Dim r As Double, g As Double, b As Double
    Dim sigma As Double
    Dim item As String
    Dim count As Long

    total = 0
    On Error Resume Next
    total = Material.GetNumberOfMaterials()
    If Err.Number <> 0 Then
        warnings = AddItem(warnings, JStr("Could not enumerate CST materials: " & Err.Description))
        Err.Clear
        On Error GoTo 0
        CollectMaterials = 0
        Exit Function
    End If
    On Error GoTo 0

    count = 0
    For i = 0 To total - 1
        name = ""
        cstType = ""
        On Error Resume Next
        name = Material.GetNameOfMaterialFromIndex(i)
        If Len(name) > 0 Then
            If Not Material.IsBackgroundMaterial(name) Then
                cstType = Material.GetTypeOfMaterial(name)
            Else
                name = ""
            End If
        End If
        Err.Clear
        On Error GoTo 0
        If Len(name) = 0 Then GoTo NextMaterial

        Select Case LCase$(cstType)
            Case "pec"
                kind = "pec"
            Case "lossy metal", "ohmic sheet"
                kind = "conducting_sheet"
            Case "normal"
                kind = "dielectric"
            Case Else
                kind = "dielectric"
                warnings = AddItem(warnings, JStr( _
                    "Material '" & name & "': CST type '" & cstType & _
                    "' is exported as a dielectric. Review epsilon/conductivity after import."))
        End Select

        epsX = 1#: epsY = 1#: epsZ = 1#
        muX = 1#: muY = 1#: muZ = 1#
        sigX = 0#: sigY = 0#: sigZ = 0#
        r = -1#: g = -1#: b = -1#
        On Error Resume Next
        Call Material.GetEpsilon(name, epsX, epsY, epsZ)
        Call Material.GetMu(name, muX, muY, muZ)
        Call Material.GetSigma(name, sigX, sigY, sigZ)
        Call Material.GetColour(name, r, g, b)
        Err.Clear
        On Error GoTo 0

        sigma = Max3(Abs(sigX), Abs(sigY), Abs(sigZ))
        If kind = "conducting_sheet" And sigma <= 0# Then
            warnings = AddItem(warnings, JStr( _
                "Material '" & name & "': no conductivity was returned; import will use copper's 5.8e7 S/m."))
        End If

        item = "{" & _
            JStr("name") & ":" & JStr(name) & "," & _
            JStr("kind") & ":" & JStr(kind) & "," & _
            JStr("epsilon") & ":" & JNum(epsX) & "," & _
            JStr("permeability") & ":" & JNum(muX) & "," & _
            JStr("kappa") & ":" & JNum(sigma)
        If r >= 0# And g >= 0# And b >= 0# Then
            item = item & "," & JStr("color") & ":[" & _
                   JNum(r) & "," & JNum(g) & "," & JNum(b) & ",1.0]"
        End If
        item = item & "}"
        items = AddItem(items, item)
        count = count + 1

NextMaterial:
    Next i

    warnings = AddItem(warnings, JStr( _
        "CST loss tangents are not queried by this macro; conductivity (S/m) is exported. " & _
        "Review dielectric tan(delta) after import."))
    CollectMaterials = count
End Function

' ---------------------------------------------------------------------------
' Solids
' ---------------------------------------------------------------------------
Private Function CollectSolids(ByVal stlDir As String, ByRef items As String, _
                               ByRef warnings As String) As Long
    Dim total As Long
    Dim i As Long
    Dim globalName As String
    Dim component As String
    Dim solidName As String
    Dim material As String
    Dim isSolid As Boolean
    Dim kind As String
    Dim fileBase As String
    Dim stlPath As String
    Dim relPath As String
    Dim errNumber As Long
    Dim errText As String
    Dim item As String
    Dim count As Long

    On Error Resume Next
    Solid.UpdateAllBodyTypes
    Err.Clear
    On Error GoTo 0

    total = 0
    On Error Resume Next
    total = Solid.GetNumberOfShapes()
    If Err.Number <> 0 Then
        warnings = AddItem(warnings, JStr("Could not enumerate CST solids: " & Err.Description))
        Err.Clear
        On Error GoTo 0
        CollectSolids = 0
        Exit Function
    End If
    On Error GoTo 0

    count = 0
    For i = 0 To total - 1
        globalName = ""
        On Error Resume Next
        globalName = Solid.GetNameOfShapeFromIndex(i)
        Err.Clear
        On Error GoTo 0
        If Len(globalName) = 0 Then GoTo NextSolid

        SplitGlobalName globalName, component, solidName
        If Len(component) = 0 Then component = "default"
        If Len(solidName) = 0 Then solidName = globalName

        material = ""
        On Error Resume Next
        material = Solid.GetMaterialNameForShape(globalName)
        Err.Clear
        On Error GoTo 0
        If Len(material) = 0 Then material = "PEC"

        isSolid = True
        On Error Resume Next
        isSolid = Solid.IsSolidShape(globalName)
        Err.Clear
        On Error GoTo 0
        If isSolid Then
            kind = "solid"
        Else
            kind = "sheet"
        End If

        fileBase = SafeName(component) & "__" & SafeName(solidName)
        stlPath = stlDir & "\" & fileBase & ".stl"
        On Error Resume Next
        If Dir(stlPath) <> "" Then
            fileBase = fileBase & "_" & CStr(i + 1)
            stlPath = stlDir & "\" & fileBase & ".stl"
        End If
        Err.Clear
        On Error GoTo 0

        errNumber = 0
        errText = ""
        On Error Resume Next
        If Dir(stlPath) <> "" Then Kill stlPath
        With STL
            .Reset
            .FileName stlPath
            .Name solidName
            .Component component
            .ExportFileUnits "m"
            .Write
        End With
        errNumber = Err.Number
        errText = Err.Description
        Err.Clear
        On Error GoTo 0

        If errNumber <> 0 Or Dir(stlPath) = "" Then
            warnings = AddItem(warnings, JStr( _
                "STL export failed for " & globalName & ": " & errText))
            GoTo NextSolid
        End If

        relPath = "stl/" & fileBase & ".stl"
        item = "{" & _
            JStr("name") & ":" & JStr(globalName) & "," & _
            JStr("component") & ":" & JStr(component) & "," & _
            JStr("material") & ":" & JStr(material) & "," & _
            JStr("kind") & ":" & JStr(kind) & "," & _
            JStr("file") & ":" & JStr(relPath) & "}"
        items = AddItem(items, item)
        count = count + 1

NextSolid:
    Next i

    CollectSolids = count
End Function

' ---------------------------------------------------------------------------
' Ports
' ---------------------------------------------------------------------------
Private Function CollectPorts(ByRef items As String, ByRef warnings As String) As Long
    Dim total As Long
    Dim i As Long
    Dim portNumber As Long
    Dim portType As String
    Dim portLabel As String
    Dim item As String
    Dim count As Long
    Dim gToSI As Double
    Dim x0 As Double, y0 As Double, z0 As Double
    Dim x1 As Double, y1 As Double, z1 As Double
    Dim orient As Long
    Dim dxmin As Double, dxmax As Double
    Dim dymin As Double, dymax As Double
    Dim dzmin As Double, dzmax As Double
    Dim direction As String
    Dim errNumber As Long
    Dim errText As String

    gToSI = Units.GetGeometryUnitToSI
    total = 0
    On Error Resume Next
    total = Port.StartPortNumberIteration
    If Err.Number <> 0 Then
        warnings = AddItem(warnings, JStr("Could not enumerate CST ports: " & Err.Description))
        Err.Clear
        On Error GoTo 0
        CollectPorts = 0
        Exit Function
    End If
    On Error GoTo 0

    count = 0
    For i = 1 To total
        portNumber = 0
        portType = ""
        portLabel = ""
        On Error Resume Next
        portNumber = Port.GetNextPortNumber
        If portNumber > 0 Then
            portType = Port.GetType(portNumber)
            portLabel = Port.GetLabel(portNumber)
        End If
        Err.Clear
        On Error GoTo 0
        If portNumber <= 0 Then GoTo NextPort
        If Len(portLabel) = 0 Then portLabel = "port_" & CStr(portNumber)

        If LCase$(portType) = "discrete" Then
            x0 = 0#: y0 = 0#: z0 = 0#
            x1 = 0#: y1 = 0#: z1 = 0#
            On Error Resume Next
            Call DiscretePort.GetCoordinates(portNumber, x0, y0, z0, x1, y1, z1)
            Err.Clear
            On Error GoTo 0
            If (Abs(x1 - x0) + Abs(y1 - y0) + Abs(z1 - z0)) <= 0# Then
                warnings = AddItem(warnings, JStr( _
                    "Discrete port '" & portLabel & "': no coordinates returned; skipped."))
                GoTo NextPort
            End If
            item = "{" & _
                JStr("name") & ":" & JStr(portLabel) & "," & _
                JStr("type") & ":" & JStr("lumped") & "," & _
                JStr("start") & ":" & JVec3(x0 * gToSI, y0 * gToSI, z0 * gToSI) & "," & _
                JStr("stop") & ":" & JVec3(x1 * gToSI, y1 * gToSI, z1 * gToSI) & "," & _
                JStr("resistance") & ":50.0," & _
                JStr("excite") & ":false}"
            items = AddItem(items, item)
            count = count + 1
            GoTo NextPort
        End If

        ' Waveguide port: the mesh coordinates are the most useful box.  They
        ' are only available after a mesh exists; otherwise the port is skipped
        ' with a warning instead of guessing its dimensions.
        orient = -1
        dxmin = 0#: dxmax = 0#
        dymin = 0#: dymax = 0#
        dzmin = 0#: dzmax = 0#
        errNumber = 0
        errText = ""
        On Error Resume Next
        Call Port.GetPortMeshCoordinates(portNumber, orient, dxmin, dxmax, _
            dymin, dymax, dzmin, dzmax)
        errNumber = Err.Number
        errText = Err.Description
        Err.Clear
        On Error GoTo 0
        If errNumber <> 0 Then
            warnings = AddItem(warnings, JStr( _
                "Waveguide port '" & portLabel & "': no mesh coordinates (" & errText & _
                "). Build the mesh in CST and run the export again."))
            GoTo NextPort
        End If
        If (Abs(dxmax - dxmin) + Abs(dymax - dymin) + Abs(dzmax - dzmin)) <= 0# Then
            warnings = AddItem(warnings, JStr( _
                "Waveguide port '" & portLabel & "': empty port box. Build the mesh in CST and run the export again."))
            GoTo NextPort
        End If

        Select Case orient
            Case 0, 1
                direction = "x"
            Case 2, 3
                direction = "y"
            Case Else
                direction = "z"
        End Select
        item = "{" & _
            JStr("name") & ":" & JStr(portLabel) & "," & _
            JStr("type") & ":" & JStr("rect_waveguide") & "," & _
            JStr("start") & ":" & JVec3(dxmin * gToSI, dymin * gToSI, dzmin * gToSI) & "," & _
            JStr("stop") & ":" & JVec3(dxmax * gToSI, dymax * gToSI, dzmax * gToSI) & "," & _
            JStr("direction") & ":" & JStr(direction) & "," & _
            JStr("mode") & ":" & JStr("TE10") & "," & _
            JStr("excite") & ":false}"
        items = AddItem(items, item)
        count = count + 1

NextPort:
    Next i

    CollectPorts = count
End Function

' ---------------------------------------------------------------------------
' Helpers
' ---------------------------------------------------------------------------
Private Sub SplitGlobalName(ByVal globalName As String, ByRef component As String, _
                            ByRef solidName As String)
    Dim p As Long
    p = InStr(globalName, ":")
    If p > 0 Then
        component = Left$(globalName, p - 1)
        solidName = Mid$(globalName, p + 1)
    Else
        component = "default"
        solidName = globalName
    End If
End Sub

Private Function StripCstExtension(ByVal path As String) As String
    If LCase$(Right$(path, 4)) = ".cst" Then
        StripCstExtension = Left$(path, Len(path) - 4)
    Else
        StripCstExtension = path
    End If
End Function

Private Function BaseName(ByVal path As String) As String
    Dim p As Long
    p = InStrRev(path, "\")
    If p > 0 Then
        BaseName = Mid$(path, p + 1)
    Else
        BaseName = path
    End If
End Function

Private Function FolderExists(ByVal path As String) As Boolean
    If Len(path) = 0 Then Exit Function
    On Error Resume Next
    FolderExists = (Len(Dir(path, vbDirectory)) > 0)
    Err.Clear
    On Error GoTo 0
End Function

Private Sub ClearStlFolder(ByVal folder As String)
    Dim fileName As String
    fileName = Dir(folder & "\*.*")
    Do While Len(fileName) > 0
        If fileName <> "." And fileName <> ".." Then
            On Error Resume Next
            Kill folder & "\" & fileName
            Err.Clear
            On Error GoTo 0
        End If
        fileName = Dir()
    Loop
End Sub

Private Function WriteTextFile(ByVal path As String, ByVal text As String) As Boolean
    Dim handle As Integer
    handle = FreeFile
    On Error Resume Next
    Open path For Output As #handle
    If Err.Number <> 0 Then
        Err.Clear
        On Error GoTo 0
        Exit Function
    End If
    Print #handle, text
    Close #handle
    If Err.Number <> 0 Then
        Err.Clear
        On Error GoTo 0
        Exit Function
    End If
    On Error GoTo 0
    WriteTextFile = True
End Function

Private Function SafeName(ByVal value As String) As String
    Dim i As Long
    Dim ch As String
    Dim out As String
    For i = 1 To Len(value)
        ch = Mid$(value, i, 1)
        If (ch >= "a" And ch <= "z") Or (ch >= "A" And ch <= "Z") Or _
           (ch >= "0" And ch <= "9") Or ch = "_" Or ch = "-" Or ch = "." Then
            out = out & ch
        Else
            out = out & "_"
        End If
    Next i
    If Len(out) = 0 Then out = "object"
    SafeName = out
End Function

Private Function Max3(ByVal a As Double, ByVal b As Double, ByVal c As Double) As Double
    Max3 = a
    If b > Max3 Then Max3 = b
    If c > Max3 Then Max3 = c
End Function

Private Function AddItem(ByVal current As String, ByVal item As String) As String
    If Len(current) = 0 Then
        AddItem = item
    Else
        AddItem = current & "," & item
    End If
End Function

Private Function JVec3(ByVal x As Double, ByVal y As Double, ByVal z As Double) As String
    JVec3 = "[" & JNum(x) & "," & JNum(y) & "," & JNum(z) & "]"
End Function

Private Function JNum(ByVal value As Double) As String
    Dim text As String
    text = CStr(value)
    text = Replace(text, ",", ".")
    JNum = text
End Function

Private Function JStr(ByVal value As String) As String
    JStr = """" & JsonEscape(value) & """"
End Function

Private Function JsonEscape(ByVal value As String) As String
    Dim i As Long
    Dim ch As String
    Dim code As Long
    Dim out As String
    For i = 1 To Len(value)
        ch = Mid$(value, i, 1)
        Select Case ch
            Case "\"
                out = out & "\\"
            Case """"
                out = out & "\" & Chr$(34)
            Case vbCr
                out = out & "\r"
            Case vbLf
                out = out & "\n"
            Case vbTab
                out = out & "\t"
            Case Else
                code = AscW(ch)
                If code < 0 Then code = code + 65536
                If code < 32 Or code > 126 Then
                    out = out & "\u" & Right$("0000" & Hex$(code), 4)
                Else
                    out = out & ch
                End If
        End Select
    Next i
    JsonEscape = out
End Function
